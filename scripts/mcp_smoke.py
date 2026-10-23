#!/usr/bin/env python3
"""MCP conformance smoke test for DocOps Commander (stdlib only).

Talks to the self-hosted MCP server over Streamable HTTP exactly the way an
external agent would: initialize -> initialized -> tools/list -> tools/call.

Start the server first:

    python -m uvicorn backend.main:app --host 127.0.0.1 --port 8010

Then run:

    python scripts/mcp_smoke.py

Exit code is 0 only when every assertion passes.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

URL = "http://127.0.0.1:8010/mcp"
PROTOCOL = "2025-11-25"
EXPECTED_TOOLS = {
    "orchestrate",
    "get_board",
    "diagnose_jobs",
    "prioritise_cases",
    "draft_resolution_plan",
    "approve_plan",
    "reject_plan",
    "recall_decisions",
    "reset_demo",
}

session_id: str | None = None
request_id = 0

passed: list[str] = []
failed: list[str] = []


def _post(payload: dict) -> tuple[int, dict | None, dict]:
    request = urllib.request.Request(
        URL,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **({"Mcp-Session-Id": session_id} if session_id else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read()
            return (
                response.status,
                json.loads(raw) if raw else None,
                dict(response.headers),
            )
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        body = json.loads(raw) if raw else None
        return exc.code, body, dict(exc.headers)


def _header(headers: dict, name: str) -> str | None:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return None


def rpc(method: str, params: dict | None = None, notify: bool = False) -> dict | None:
    global session_id, request_id
    payload: dict = {"jsonrpc": "2.0", "method": method}
    if not notify:
        request_id += 1
        payload["id"] = request_id
    if params is not None:
        payload["params"] = params
    _, body, headers = _post(payload)
    issued = _header(headers, "mcp-session-id")
    if issued:
        session_id = issued
    return body


def call(name: str, arguments: dict | None = None) -> dict:
    body = rpc("tools/call", {"name": name, "arguments": arguments or {}})
    return (body or {}).get("result", {}).get("structuredContent", {})


def check(condition: bool, label: str) -> None:
    if condition:
        passed.append(label)
        print(f"  PASS  {label}")
    else:
        failed.append(label)
        print(f"  FAIL  {label}")


def tile(state: dict, label: str) -> dict:
    for item in state.get("stats", {}).get("tiles", []):
        if item.get("label") == label:
            return item
    return {}


def main() -> int:
    print("\nDocOps Commander - MCP conformance smoke test\n" + "-" * 46)

    # 1. Handshake -----------------------------------------------------------
    print("\n[1] Handshake")
    init = rpc(
        "initialize",
        {
            "protocolVersion": PROTOCOL,
            "capabilities": {},
            "clientInfo": {"name": "mcp-smoke", "version": "1.0.0"},
        },
    )
    result = (init or {}).get("result", {})
    check((init or {}).get("jsonrpc") == "2.0", "initialize is JSON-RPC 2.0")
    check(result.get("protocolVersion") == PROTOCOL, f"negotiates {PROTOCOL}")
    check(bool(session_id), "issues Mcp-Session-Id")
    check(result.get("capabilities", {}).get("tools") is not None, "advertises tools capability")
    check(bool(result.get("serverInfo", {}).get("name")), "returns serverInfo")

    ack_status, _, _ = _post({"jsonrpc": "2.0", "method": "notifications/initialized"})
    check(ack_status == 202, "notifications/initialized -> 202")

    ping = rpc("ping")
    check((ping or {}).get("result") == {}, "ping responds")

    # 2. Discovery -----------------------------------------------------------
    print("\n[2] Tool discovery")
    body = rpc("tools/list", {}) or {}
    tools = body.get("result", {}).get("tools", [])
    names = {tool.get("name") for tool in tools}
    check(names == EXPECTED_TOOLS, f"{len(EXPECTED_TOOLS)} tools exposed")
    check(all(tool.get("inputSchema", {}).get("type") == "object" for tool in tools), "every tool has an object inputSchema")
    check(all(tool.get("description") for tool in tools), "every tool has a description")

    # 3. Read tools ----------------------------------------------------------
    print("\n[3] Read tools")
    board = call("reset_demo")
    state = board.get("state", board)
    check(tile(state, "Failing now").get("value") == "11", "board starts with 11 failing")
    check(len(board.get("cards", [])) >= 11, "cards include every open case")

    diagnosis = call("diagnose_jobs", {"doc_type": "invoice"})
    check(len(diagnosis.get("cases", [])) == 9, "diagnose_jobs scopes to 9 invoice cases")
    check(
        [step.get("tool") for step in diagnosis.get("trace", [])]
        == ["diagnose_jobs", "prioritise_cases"],
        "diagnose traces both tools",
    )

    ranked = call("prioritise_cases", {"limit": 5})
    check(len(ranked.get("cases", [])) == 5, "prioritise_cases honours its limit")

    # 4. Approval gate -------------------------------------------------------
    print("\n[4] Approval gate")
    plan = call("draft_resolution_plan")
    action = plan.get("action") or {}
    action_id = plan.get("action_id") or action.get("action_id", "")
    check(action.get("requires_approval") is True, "plan requires approval")
    check(len(action.get("segments", [])) == 3, "plan has 3 segments")
    board_now = call("get_board")
    check(tile(board_now, "Failing now").get("value") == "11", "drafting changes nothing")

    approved = call("approve_plan", {"action_id": action_id, "actor": "mcp-smoke"})
    check("dispatched 5 retries" in approved.get("summary", ""), "approval summary is accurate")
    check(len(approved.get("changed_jobs", [])) == 11, "approval moved 11 jobs")
    check(tile(approved.get("state", {}), "Failing now").get("value") == "0", "board clears after approval")

    second = rpc("tools/call", {"name": "approve_plan", "arguments": {"action_id": action_id}})
    check((second or {}).get("result", {}).get("isError") is True, "double approval is refused")

    # 5. Memory --------------------------------------------------------------
    print("\n[5] Memory")
    recalled = call("recall_decisions", {"vendor": "Orion"})
    matches = recalled.get("matches", [])
    check(len(matches) >= 1, "approved decision is recalled")
    check(any("JOB" in json.dumps(match) for match in matches), "recall returns the affected job ids")

    # 6. Protocol errors -----------------------------------------------------
    print("\n[6] Protocol errors")
    unknown = rpc("tools/call", {"name": "does_not_exist", "arguments": {}}) or {}
    check(unknown.get("error", {}).get("code") == -32602, "unknown tool -> -32602 Invalid params")
    bad_method = rpc("totally/unknown") or {}
    check(bad_method.get("error", {}).get("code") == -32601, "unknown method -> -32601 Method not found")

    request = urllib.request.Request(URL, method="GET")
    try:
        status = urllib.request.urlopen(request, timeout=10).status
    except urllib.error.HTTPError as exc:
        status = exc.code
    check(status == 405, "GET /mcp -> 405 (POST-only transport)")

    # Cleanup ----------------------------------------------------------------
    reset = call("reset_demo")
    check(tile(reset.get("state", reset), "Failing now").get("value") == "11", "reset_demo restores the board")

    total = len(passed) + len(failed)
    print("\n" + "-" * 46)
    if failed:
        print(f"{len(failed)} of {total} checks FAILED:")
        for label in failed:
            print(f"  - {label}")
        return 1
    print(f"ALL {total} MCP CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
