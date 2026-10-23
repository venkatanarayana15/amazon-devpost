"""DocOps Commander — MCP server over Streamable HTTP.

Implements the Model Context Protocol (spec 2025-11-25) at ``POST /mcp``:

* ``initialize`` handshake with protocol-version negotiation
* ``notifications/initialized`` acknowledgement
* ``ping``
* ``tools/list`` — the agent's tool surface
* ``tools/call`` — invoke a tool, returning ``content`` + ``structuredContent``

This is the Alexa+ track's primary delivery surface: a self-hosted MCP server
speaking JSON-RPC 2.0 over the Streamable HTTP transport. The bundled web
console is a real MCP client that talks to this same endpoint, so the demo
shows the protocol actually being used rather than merely described.
"""

from __future__ import annotations

import json
import threading
import uuid
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from . import config, dataset, db, planner, tools

router = APIRouter()

PROTOCOL_VERSION = "2025-11-25"
SERVER_NAME = "docops-commander"
SERVER_VERSION = "1.0.0"

_SESSIONS: dict[str, dict[str, Any]] = {}


# --- helpers ---------------------------------------------------------------


def _ok(msg_id: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _err(msg_id: Any, code: int, message: str, data: Any = None) -> dict:
    payload: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        payload["data"] = data
    return {"jsonrpc": "2.0", "id": msg_id, "error": payload}


def _text(value: Any) -> dict:
    if isinstance(value, str):
        return {"type": "text", "text": value}
    return {"type": "text", "text": json.dumps(value, indent=2, default=str)}


def _tool_result(summary: str, structured: dict, is_error: bool = False) -> dict:
    return {
        "content": [_text(summary), _text(structured)],
        "structuredContent": structured,
        "isError": is_error,
    }


def _schedule_recovery(job_ids: list[str]) -> None:
    def _recover(job_id: str) -> None:
        job = dataset.get_job(job_id)
        if not job or job["status"] != "retrying":
            return
        dataset.set_status(job_id, "recovered")
        db.add_audit(
            event="retry_recovered",
            actor="agent",
            detail=f"{job_id} parsed successfully on retry and is now in the posting queue",
            job_ids=[job_id],
        )

    for job_id in job_ids:
        timer = threading.Timer(
            config.SIMULATION_RETRY_RECOVERY_SECONDS, _recover, args=(job_id,)
        )
        timer.daemon = True
        timer.start()


def _board_payload() -> dict:
    return planner.state_payload()


def _approve(action_id: str, actor: str = "operator") -> dict:
    action = db.get_pending_action(action_id)
    if action is None:
        return {"ok": False, "error": "Unknown action. It may have expired with a reset."}
    if action["status"] != "pending":
        return {"ok": False, "error": f"Action is already {action['status']}."}
    if not db.resolve_pending_action(action_id, "approved"):
        return {"ok": False, "error": "Action was resolved by another operator."}

    result = tools.execute_action(action, actor=actor)
    retried = [
        j
        for j in result["changed_jobs"]
        if dataset.get_job(j) and dataset.get_job(j)["status"] == "retrying"
    ]
    _schedule_recovery(retried)

    counts = {item["action_type"]: item["count"] for item in result["executed"]}

    def plural(n: int, singular: str, plural_form: str | None = None) -> str:
        return f"{n} {singular if n == 1 else (plural_form or singular + 's')}"

    parts = []
    if counts.get("retry_jobs"):
        parts.append(f"dispatched {plural(counts['retry_jobs'], 'retry', 'retries')}")
    if counts.get("queue_review"):
        parts.append(f"queued {plural(counts['queue_review'], 'case')} for review")
    if counts.get("escalate"):
        parts.append(f"escalated {plural(counts['escalate'], 'case')}")
    if len(parts) > 1:
        summary = ", ".join(parts[:-1]) + " and " + parts[-1]
    elif parts:
        summary = parts[0]
    else:
        summary = "executed with no job changes"

    db.set_decision_outcome(action_id, "approved", {"result": summary, "actor": actor})
    db.add_audit(
        event="action_approved",
        actor=actor,
        session_id=action["session_id"],
        detail=f"{action['title']} — {summary}",
        job_ids=result["changed_jobs"],
    )
    db.touch_session(action["session_id"])
    return {
        "ok": True,
        "action_id": action_id,
        "summary": summary,
        "changes": result["executed"],
        "changed_jobs": result["changed_jobs"],
        "state": _board_payload(),
    }


def _reject(action_id: str, reason: str, actor: str = "operator") -> dict:
    action = db.get_pending_action(action_id)
    if action is None:
        return {"ok": False, "error": "Unknown action. It may have expired with a reset."}
    if action["status"] != "pending":
        return {"ok": False, "error": f"Action is already {action['status']}."}
    if not db.resolve_pending_action(action_id, "rejected"):
        return {"ok": False, "error": "Action was resolved by another operator."}

    db.set_decision_outcome(action_id, "rejected", {"reason": reason, "actor": actor})
    db.add_audit(
        event="action_rejected",
        actor=actor,
        session_id=action["session_id"],
        detail=f"{action['title']} — {reason}",
        job_ids=[jid for seg in action["segments"] for jid in seg["job_ids"]],
    )
    return {
        "ok": True,
        "action_id": action_id,
        "state": _board_payload(),
    }


# --- tool surface ----------------------------------------------------------

TOOLS: list[dict[str, Any]] = [
    {
        "name": "orchestrate",
        "title": "Orchestrate a natural-language request",
        "description": (
            "Entry point for a spoken or typed operator request. Routes the utterance "
            "to diagnosis, prioritisation, planning, execution or memory recall, and "
            "returns a narrated reply plus the full UI payload (board cards, stats, "
            "tool trace, pending approval)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "utterance": {
                    "type": "string",
                    "description": "What the operator said, e.g. 'Why are today's invoice jobs failing?'",
                },
                "session_id": {
                    "type": "string",
                    "description": "Optional conversation id to keep context across turns.",
                },
            },
            "required": ["utterance"],
        },
    },
    {
        "name": "get_board",
        "title": "Get the operations board",
        "description": (
            "Return every open document case with priority, SLA pressure, failure "
            "pattern and recommended handling, plus the aggregate stats, decision "
            "memory and recent audit trail."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "diagnose_jobs",
        "title": "Diagnose failing jobs",
        "description": (
            "Group failing document jobs by failure pattern and report SLA pressure "
            "and dollar exposure for the group."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "doc_type": {"type": "string", "description": "Optional document type filter, e.g. invoice."},
                "vendor": {"type": "string", "description": "Optional vendor filter."},
            },
        },
    },
    {
        "name": "prioritise_cases",
        "title": "Prioritise open cases",
        "description": "Rank open cases by SLA risk, value and failure class.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            },
        },
    },
    {
        "name": "draft_resolution_plan",
        "title": "Draft a resolution plan",
        "description": (
            "Build a three-segment, policy-cited plan (retry / human review / escalate) "
            "without executing anything. Returns an action id that must be approved."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "approve_plan",
        "title": "Approve and execute a plan",
        "description": (
            "The only path by which an action reaches production. Executes the drafted "
            "plan and records the decision in durable memory."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action_id": {"type": "string", "description": "Action id from draft_resolution_plan."},
                "actor": {"type": "string", "description": "Human approver name.", "default": "operator"},
            },
            "required": ["action_id"],
        },
    },
    {
        "name": "reject_plan",
        "title": "Reject a plan",
        "description": "Decline a drafted plan. Nothing executes; the rejection is remembered.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action_id": {"type": "string"},
                "reason": {"type": "string", "default": "Rejected by operator"},
            },
            "required": ["action_id"],
        },
    },
    {
        "name": "recall_decisions",
        "title": "Recall prior decisions",
        "description": (
            "Search durable decision memory by vendor or keyword and return the earlier "
            "plan, its outcome, the policies applied and the jobs involved."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "vendor": {"type": "string", "description": "Vendor name, e.g. Orion."},
                "query": {"type": "string", "description": "Free-text query if no vendor is known."},
            },
        },
    },
    {
        "name": "reset_demo",
        "title": "Reset the demo",
        "description": "Restore the synthetic 17-job dataset and clear session memory.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _call_tool(name: str, args: dict) -> dict:
    """Dispatch a tools/call request. Returns an MCP tool result."""

    if name == "orchestrate":
        utterance = str(args.get("utterance", "")).strip()
        if not utterance:
            return _tool_result("No utterance supplied.", {"error": "utterance is required"}, True)
        session_id = str(args.get("session_id") or f"s_{uuid.uuid4().hex[:12]}")
        result = planner.handle_command(session_id, utterance)
        result["session_id"] = session_id
        return _tool_result(result.get("reply", ""), result)

    if name == "get_board":
        payload = _board_payload()
        stats = payload.get("stats", {})
        summary = (
            f"{stats.get('failed', 0)} failing cases, "
            f"{stats.get('value_at_risk_label', '$0')} at risk, "
            f"{stats.get('sla_at_risk', 0)} inside the SLA window."
        )
        return _tool_result(summary, payload)

    if name == "diagnose_jobs":
        trace = tools.Trace()
        _jobs, cases = tools.diagnose_jobs(
            trace,
            doc_type=(args.get("doc_type") or None),
            vendor=(args.get("vendor") or None),
        )
        cases = tools.prioritise(trace, cases)
        stats = planner.build_stats(_jobs)
        summary = f"{len(cases)} failing case(s) grouped by failure pattern."
        return _tool_result(summary, {"cases": cases, "stats": stats, "trace": trace.steps})

    if name == "prioritise_cases":
        trace = tools.Trace()
        _jobs, cases = tools.diagnose_jobs(trace)
        cases = tools.prioritise(trace, cases)
        limit = max(1, min(50, int(args.get("limit") or 10)))
        ranked = cases[:limit]
        return _tool_result(
            f"Top {len(ranked)} cases ranked by SLA risk and value.",
            {"cases": ranked, "trace": trace.steps},
        )

    if name == "draft_resolution_plan":
        trace = tools.Trace()
        _jobs, cases = tools.diagnose_jobs(trace)
        cases = tools.prioritise(trace, cases)
        batches = tools.build_batches(trace, cases)
        session_id = str(args.get("session_id") or f"s_{uuid.uuid4().hex[:12]}")
        action = tools.draft_action(trace, session_id=session_id, batches=batches)
        if not action:
            return _tool_result("Nothing to plan: no failing cases remain.", {"action": None})
        # Record the pending decision exactly like the conversational path, so a
        # plan drafted through MCP is recalled and audited after approval.
        all_cases = [case for group in batches.values() for case in group]
        db.record_decision(
            session_id=session_id,
            action_id=action["action_id"],
            title=action["title"],
            action_types=[segment["action_type"] for segment in action["segments"]],
            job_ids=[case["job_id"] for case in all_cases],
            vendors=[case["vendor"] for case in all_cases],
            policies=action["policies"],
            outcome="pending",
            detail={"risk_note": action["risk_note"]},
        )
        db.add_audit(
            event="plan_proposed",
            actor="agent",
            session_id=session_id,
            detail=action["title"],
            job_ids=[case["job_id"] for case in all_cases],
        )
        summary = (
            f"Drafted '{action['title']}' across {len(action['segments'])} segments, "
            f"citing {len(action['policies'])} policies. Awaiting approval."
        )
        return _tool_result(
            summary,
            {"action": action, "action_id": action["action_id"], "trace": trace.steps},
        )

    if name == "approve_plan":
        action_id = str(args.get("action_id", "")).strip()
        actor = str(args.get("actor") or "operator")
        if not action_id:
            return _tool_result("action_id is required.", {"error": "action_id is required"}, True)
        result = _approve(action_id, actor)
        if not result.get("ok"):
            return _tool_result(result.get("error", "Approval failed."), result, True)
        return _tool_result(
            f"Approved. {result['summary'][0].upper() + result['summary'][1:]}.",
            result,
        )

    if name == "reject_plan":
        action_id = str(args.get("action_id", "")).strip()
        reason = str(args.get("reason") or "Rejected by operator")
        if not action_id:
            return _tool_result("action_id is required.", {"error": "action_id is required"}, True)
        result = _reject(action_id, reason)
        if not result.get("ok"):
            return _tool_result(result.get("error", "Rejection failed."), result, True)
        return _tool_result("Rejected. Nothing executed; the decision was recorded.", result)

    if name == "recall_decisions":
        vendor = (args.get("vendor") or "").strip()
        query = (args.get("query") or "").strip() or vendor or "decision"
        trace = tools.Trace()
        hits = tools.recall_memory(trace, query)
        decisions = db.all_decisions(limit=20)
        return _tool_result(
            f"{len(hits)} decision(s) matched '{query}'.",
            {"matches": hits, "decisions": decisions, "trace": trace.steps},
        )

    if name == "reset_demo":
        db.reset_session_state()
        db.add_audit(event="demo_reset", actor="operator", detail="Synthetic dataset and memory restored")
        return _tool_result("Demo reset. 17 synthetic jobs and memory restored.", _board_payload())

    raise KeyError(name)


# --- JSON-RPC dispatch -----------------------------------------------------


def _dispatch(message: dict) -> dict | None:
    method = message.get("method")
    msg_id = message.get("id")
    params = message.get("params") or {}
    is_notification = "id" not in message

    if method == "initialize":
        return _ok(
            msg_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": (
                    "DocOps Commander turns document-processing failures into "
                    "approval-gated operational actions. Call orchestrate with the "
                    "operator's utterance, then approve_plan only after a human says yes."
                ),
            },
        )

    if method in ("notifications/initialized", "notifications/cancelled"):
        return None

    if method == "ping":
        return _ok(msg_id, {})

    if method == "tools/list":
        return _ok(msg_id, {"tools": TOOLS})

    if method == "resources/list":
        return _ok(msg_id, {"resources": []})

    if method == "prompts/list":
        return _ok(msg_id, {"prompts": []})

    if method == "tools/call":
        if is_notification:
            return None
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return _err(msg_id, -32602, "tools/call arguments must be an object")
        try:
            result = _call_tool(str(name), arguments)
        except KeyError:
            return _err(msg_id, -32602, f"Unknown tool: {name}")
        except Exception as exc:  # pragma: no cover - defensive
            return _err(msg_id, -32603, f"Tool execution failed: {exc}")
        return _ok(msg_id, result)

    if is_notification:
        return None
    return _err(msg_id, -32601, f"Method not found: {method}")


@router.post("/mcp")
async def mcp_endpoint(request: Request) -> Response:
    """Streamable HTTP transport: one JSON-RPC message (or a batch) per POST."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            _err(None, -32700, "Parse error"), status_code=400, media_type="application/json"
        )

    raw_session = request.headers.get("mcp-session-id")
    new_session = None
    if not raw_session:
        new_session = f"mcp_{uuid.uuid4().hex[:16]}"
        _SESSIONS[new_session] = {"created": db.utcnow()}
    session_id = raw_session or new_session

    headers = {"Mcp-Session-Id": session_id}
    if new_session:
        headers["Mcp-Session-Id"] = new_session

    batch = isinstance(body, list)
    messages = body if batch else [body]

    responses = []
    for message in messages:
        if not isinstance(message, dict):
            responses.append(_err(None, -32600, "Invalid Request"))
            continue
        if message.get("jsonrpc") != "2.0":
            responses.append(_err(message.get("id"), -32600, "Invalid Request: jsonrpc must be '2.0'"))
            continue
        result = _dispatch(message)
        if result is not None:
            responses.append(result)

    if not responses:
        # All notifications — accept with no body, per the transport spec.
        return Response(status_code=202, headers=headers)

    payload = responses if batch else responses[0]
    return JSONResponse(payload, headers=headers, media_type="application/json")


@router.get("/mcp")
def mcp_stream() -> Response:
    """No server-initiated stream is offered; advertise POST-only."""
    return Response(
        status_code=405,
        headers={"Allow": "POST, DELETE"},
        content=b"Method Not Allowed: this server uses request/response Streamable HTTP.",
    )


@router.delete("/mcp")
def mcp_terminate(request: Request) -> Response:
    session_id = request.headers.get("mcp-session-id")
    if session_id:
        _SESSIONS.pop(session_id, None)
    return Response(status_code=204)
