"""DocOps Commander — FastAPI application.

Serves the JSON API and the single-page operator console from the same process,
so the whole product runs with one command and no build step.
"""

from __future__ import annotations

import threading
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, dataset, db, domain, planner, tools
from .mcp_server import PROTOCOL_VERSION, TOOLS
from .mcp_server import router as mcp_router

app = FastAPI(
    title="DocOps Commander",
    version="1.0.0",
    description=(
        "A simulated Alexa+ style agent that turns document-processing failures into "
        "approval-gated operational actions with persistent decision memory."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class CommandIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    session_id: str | None = None


class ApprovalIn(BaseModel):
    session_id: str | None = None
    actor: str = Field(default="operator", max_length=120)


class RejectionIn(ApprovalIn):
    reason: str = Field(default="Rejected by operator", max_length=500)


def _new_session() -> str:
    return f"s_{uuid.uuid4().hex[:12]}"


# --- background recovery simulation ----------------------------------------


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


def _schedule_recovery(job_ids: list[str]) -> None:
    for job_id in job_ids:
        timer = threading.Timer(config.SIMULATION_RETRY_RECOVERY_SECONDS, _recover, args=(job_id,))
        timer.daemon = True
        timer.start()


# --- API -------------------------------------------------------------------


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "service": "docops-commander",
        "version": app.version,
        "jobs": len(dataset.all_jobs()),
        "llm_narration": config.LLM_ENABLED,
    }


@app.get("/api/meta")
def meta() -> dict:
    return {
        "policies": [
            {"id": pid, "name": p["name"], "rule": p["rule"]} for pid, p in domain.POLICIES.items()
        ],
        "failures": [
            {"code": code, "label": f["label"], "cause": f["cause"]}
            for code, f in domain.FAILURES.items()
        ],
        "thresholds": {
            "auto_retry_ceiling_usd": config.AUTO_RETRY_VALUE_CEILING_USD,
            "human_signoff_usd": config.HUMAN_SIGNOFF_VALUE_USD,
            "sla_at_risk_hours": config.SLA_AT_RISK_HOURS,
            "max_auto_attempts": config.MAX_AUTO_ATTEMPTS,
        },
        "vendors": dataset.vendors(),
        "demo_commands": [
            "Why are today's invoice jobs failing?",
            "Resolve safe failures and escalate high-risk documents",
            "What did we decide about Vendor Orion?",
        ],
        "mcp": {
            "endpoint": "/mcp",
            "transport": "streamable-http",
            "protocol_version": PROTOCOL_VERSION,
            "tools": [tool["name"] for tool in TOOLS],
        },
        "llm_narration": config.LLM_ENABLED,
    }


@app.get("/api/state")
def state() -> dict:
    payload = planner.state_payload()
    payload["server_time"] = db.utcnow()
    return payload


@app.post("/api/command")
def command(body: CommandIn) -> dict:
    session_id = body.session_id or _new_session()
    result = planner.handle_command(session_id, body.text.strip())
    result["session_id"] = session_id
    return result


@app.post("/api/actions/{action_id}/approve")
def approve(action_id: str, body: ApprovalIn) -> dict:
    action = db.get_pending_action(action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="Unknown action. It may have expired with a demo reset.")
    if action["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"Action is already {action['status']}.")

    if not db.resolve_pending_action(action_id, "approved"):
        raise HTTPException(status_code=409, detail="Action was resolved by another operator.")

    session_id = body.session_id or action["session_id"]
    result = tools.execute_action(action, actor=body.actor)

    retried = [j for j in result["changed_jobs"] if dataset.get_job(j) and dataset.get_job(j)["status"] == "retrying"]
    _schedule_recovery(retried)

    counts = {item["action_type"]: item["count"] for item in result["executed"]}

    def plural(n: int, singular: str, plural_form: str | None = None) -> str:
        return f"{n} {singular if n == 1 else (plural_form or singular + 's')}"

    summary_parts = []
    if counts.get("retry_jobs"):
        summary_parts.append(f"dispatched {plural(counts['retry_jobs'], 'retry', 'retries')}")
    if counts.get("queue_review"):
        summary_parts.append(f"queued {plural(counts['queue_review'], 'case')} for review")
    if counts.get("escalate"):
        summary_parts.append(f"escalated {plural(counts['escalate'], 'case')}")
    if len(summary_parts) > 1:
        summary = ", ".join(summary_parts[:-1]) + " and " + summary_parts[-1]
    else:
        summary = summary_parts[0] if summary_parts else "executed with no job changes"

    db.set_decision_outcome(action_id, "approved", {"result": summary, "actor": body.actor})
    db.add_audit(
        event="action_approved",
        actor=body.actor,
        session_id=session_id,
        detail=f"{action['title']} — {summary}",
        job_ids=result["changed_jobs"],
    )
    db.touch_session(session_id)

    payload = planner.state_payload()
    return {
        "ok": True,
        "action_id": action_id,
        "summary": summary,
        "changes": result["executed"],
        "changed_jobs": result["changed_jobs"],
        "state": payload,
        "message": f"Approved. {summary[0].upper() + summary[1:]}. Retries usually settle within a few seconds.",
    }


@app.post("/api/actions/{action_id}/reject")
def reject(action_id: str, body: RejectionIn) -> dict:
    action = db.get_pending_action(action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="Unknown action. It may have expired with a demo reset.")
    if action["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"Action is already {action['status']}.")

    if not db.resolve_pending_action(action_id, "rejected"):
        raise HTTPException(status_code=409, detail="Action was resolved by another operator.")

    session_id = body.session_id or action["session_id"]
    db.set_decision_outcome(action_id, "rejected", {"reason": body.reason, "actor": body.actor})
    db.add_audit(
        event="action_rejected",
        actor=body.actor,
        session_id=session_id,
        detail=f"{action['title']} — {body.reason}",
        job_ids=[jid for segment in action["segments"] for jid in segment["job_ids"]],
    )

    payload = planner.state_payload()
    return {
        "ok": True,
        "action_id": action_id,
        "state": payload,
        "message": "Rejected. Nothing was executed; the decision is recorded in memory.",
    }


@app.post("/api/reset")
def reset() -> dict:
    db.reset_session_state()
    db.add_audit(event="demo_reset", actor="operator", detail="Synthetic dataset and memory restored")
    payload = planner.state_payload()
    return {"ok": True, "state": payload, "message": "Demo reset. Synthetic jobs and memory restored."}


app.include_router(mcp_router)

app.mount("/", StaticFiles(directory=str(config.FRONTEND_DIR), html=True), name="ui")
