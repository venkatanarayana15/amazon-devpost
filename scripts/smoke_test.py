#!/usr/bin/env python3
"""HTTP smoke test for DocOps Commander (stdlib only, no dependencies).

Start the server first:

    python -m uvicorn backend.main:app --host 127.0.0.1 --port 8010

Then run:

    python scripts/smoke_test.py

Exit code is 0 only when every assertion passes.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8010"
RESOLVE = "Resolve safe failures and escalate high-risk documents"

passed: list[str] = []
failed: list[str] = []


def req(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(BASE + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8") or "{}"
            return response.status, json.loads(raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8") or "{}"
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"raw": raw}


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
    print("\nDocOps Commander - HTTP smoke test\n" + "-" * 44)

    # 1. Health and metadata -------------------------------------------------
    print("\n[1] Health")
    status, health = req("GET", "/api/health")
    check(status == 200, "health returns 200")
    check(health.get("ok") is True, "health.ok is true")
    check(health.get("service") == "docops-commander", "service name")
    check(health.get("jobs") == 17, "17 synthetic jobs loaded")

    status, meta = req("GET", "/api/meta")
    check(status == 200 and meta.get("mcp", {}).get("endpoint") == "/mcp", "meta advertises the MCP endpoint")

    # 2. Reset to a known board ---------------------------------------------
    print("\n[2] Reset")
    status, reset = req("POST", "/api/reset")
    check(status == 200 and reset.get("ok") is True, "reset ok")
    state = reset.get("state", {})
    check(tile(state, "Failing now").get("value") == "11", "11 failing after reset")
    check(state.get("stats", {}).get("value_at_risk_label") == "$211,470", "value at risk $211,470")
    check(tile(state, "Handled").get("value") == "0", "nothing handled yet")

    # 3. Scoped diagnosis ---------------------------------------------------
    print("\n[3] Diagnosis (invoice scope)")
    status, diagnose = req(
        "POST", "/api/command", {"text": "Why are today's invoice jobs failing?"}
    )
    reply = diagnose.get("reply", "")
    check(status == 200, "command returns 200")
    check("9 invoice documents are failing" in reply, "scoped count is 9 invoices")
    check("$211,470 at risk" in reply, "dollar exposure stated")
    check("POLICY-SLA-08" in reply, "policy citation in diagnosis")
    check("11 invoice" not in reply, "no global count leak")
    check(len(diagnose.get("options", [])) == 2, "follow-up options exclude the question just asked")

    # 4. Plan behind the approval gate --------------------------------------
    print("\n[4] Plan")
    status, planned = req("POST", "/api/command", {"text": RESOLVE})
    reply = planned.get("reply", "")
    action = planned.get("pending_action") or {}
    segments = action.get("segments", [])
    check(status == 200, "resolve returns 200")
    check(action.get("requires_approval") is True, "action requires approval")
    check(len(segments) == 3, "plan has 3 segments")
    check(
        [segment.get("action_type") for segment in segments]
        == ["retry_jobs", "queue_review", "escalate"],
        "segments are retry -> review -> escalate",
    )
    check(len(action.get("policies", [])) == 7, "7 policies cited")
    check("approval gate" in reply, "reply explains the gate")
    check(planned.get("stats", {}).get("failed") == 11, "nothing executed before approval")

    # 5. Approval executes, nothing before ----------------------------------
    print("\n[5] Approval")
    action_id = action.get("action_id", "")
    status, approved = req(
        "POST", f"/api/actions/{action_id}/approve", {"actor": "smoke-test"}
    )
    check(status == 200 and approved.get("ok") is True, "approve accepted")
    check("dispatched 5 retries" in approved.get("summary", ""), "summary uses correct plural")
    after = req("GET", "/api/state")[1]
    check(tile(after, "Failing now").get("value") == "0", "no failing jobs left")
    check(tile(after, "Handled").get("value") == "11", "11 jobs handled")

    # A second approval of the same action must not double-execute.
    status, again = req("POST", f"/api/actions/{action_id}/approve", {"actor": "smoke-test"})
    check(status == 409, "re-approving the same action is rejected")

    # 6. Simulated retry recovery -------------------------------------------
    print("\n[6] Recovery")
    time.sleep(3.6)
    recovered = req("GET", "/api/state")[1].get("stats", {}).get("recovered")
    check(recovered == 5, "5 retried documents recovered")

    # 7. Memory recall -------------------------------------------------------
    print("\n[7] Memory")
    status, recall = req(
        "POST", "/api/command", {"text": "What did we decide about Vendor Orion?"}
    )
    reply = recall.get("reply", "")
    check(recall.get("intent") == "recall", "intent classified as recall")
    check("Orion Logistics" in reply, "names the vendor")
    check("POLICY-VENDOR-04" in reply, "cites the policy applied")
    check(bool(recall.get("memory")), "matched decisions returned to the client")

    status, miss = req("POST", "/api/command", {"text": "What was the colour of the car?"})
    miss_reply = miss.get("reply", "").lower()
    check(miss.get("intent") == "recall", "unrelated question is not answered as an incident")
    check(
        "none match that phrasing" in miss_reply or "no stored decision" in miss_reply,
        "recall miss is explained instead of invented",
    )

    # 8. Help and reset ------------------------------------------------------
    print("\n[8] Help and reset")
    status, helped = req("POST", "/api/command", {"text": "help"})
    check("Alexa+" in helped.get("reply", ""), "help explains the Alexa+ framing")

    status, reset = req("POST", "/api/reset")
    check(reset.get("ok") is True, "reset works after a full run")
    check(reset.get("state", {}).get("stats", {}).get("failed") == 11, "board restored to 11 failing")

    # Summary ---------------------------------------------------------------
    total = len(passed) + len(failed)
    print("\n" + "-" * 44)
    if failed:
        print(f"{len(failed)} of {total} checks FAILED:")
        for label in failed:
            print(f"  - {label}")
        return 1
    print(f"ALL {total} CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
