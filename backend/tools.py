"""The agent's tool layer.

Each function is an explicit capability with typed inputs and a compact result
summary. `Trace` records every invocation so the UI can show the operator (and
the judges) exactly what the agent did, in order, and how long it took.
"""

from __future__ import annotations

import re
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field

from . import config, dataset, db, domain


@dataclass
class Trace:
    steps: list[dict] = field(default_factory=list)

    def add(self, tool: str, args: dict, result: str, ms: float) -> None:
        self.steps.append(
            {
                "step": len(self.steps) + 1,
                "tool": tool,
                "args": args,
                "result": result,
                "ms": round(ms, 2),
            }
        )

    def timed(self, tool: str, args: dict, result: str, started: float) -> None:
        self.add(tool, args, result, (time.perf_counter() - started) * 1000)


# --- tool: diagnose ---------------------------------------------------------


def diagnose_jobs(
    trace: Trace,
    *,
    vendor: str | None = None,
    doc_type: str | None = None,
    only_failing: bool = True,
) -> tuple[list[dict], list[dict]]:
    started = time.perf_counter()
    jobs = dataset.all_jobs()
    if only_failing:
        jobs = [j for j in jobs if j["status"] == "failed"]
    if vendor:
        jobs = [j for j in jobs if j["vendor"].lower() == vendor.lower()]
    if doc_type:
        jobs = [j for j in jobs if j["doc_type"].lower() == doc_type.lower()]

    cases = [domain.build_case(j) for j in jobs]
    cases.sort(key=lambda c: (-c["risk_score"], c["sla_due_in_hours"]))

    scope = []
    if vendor:
        scope.append(f"vendor={vendor}")
    if doc_type:
        scope.append(f"doc_type={doc_type}")
    trace.timed(
        "diagnose_jobs",
        {"status": "failed", **({"vendor": vendor} if vendor else {}), **({"doc_type": doc_type} if doc_type else {})},
        f"{len(cases)} failing case(s){' · ' + ', '.join(scope) if scope else ''}",
        started,
    )
    return jobs, cases


# --- tool: prioritise -------------------------------------------------------


def prioritise(trace: Trace, cases: list[dict]) -> list[dict]:
    started = time.perf_counter()
    ranked = sorted(cases, key=lambda c: (-c["risk_score"], c["sla_due_in_hours"]))
    band = Counter(c["priority"] for c in ranked)
    trace.timed(
        "prioritise_cases",
        {"cases": len(cases)},
        f"P0={band.get('P0', 0)} P1={band.get('P1', 0)} P2={band.get('P2', 0)}",
        started,
    )
    return ranked


# --- tool: build resolution batches ----------------------------------------


def build_batches(trace: Trace, cases: list[dict]) -> dict[str, list[dict]]:
    started = time.perf_counter()
    batches: dict[str, list[dict]] = {"auto_retry": [], "human_review": [], "escalate": []}
    for case in cases:
        batches[case["recommendation"]].append(case)
    trace.timed(
        "build_resolution_batches",
        {"cases": len(cases)},
        f"retry={len(batches['auto_retry'])} review={len(batches['human_review'])} escalate={len(batches['escalate'])}",
        started,
    )
    return batches


# --- tool: draft an approval-gated action -----------------------------------


def _segments_from_batches(batches: dict[str, list[dict]]) -> list[dict]:
    segments = []
    if batches["auto_retry"]:
        segments.append(
            {
                "action_type": "retry_jobs",
                "label": f"Re-dispatch {len(batches['auto_retry'])} safe failure(s)",
                "detail": "Idempotent retries with the enhanced OCR profile where required.",
                "job_ids": [c["job_id"] for c in batches["auto_retry"]],
                "vendors": sorted({c["vendor"] for c in batches["auto_retry"]}),
            }
        )
    if batches["human_review"]:
        segments.append(
            {
                "action_type": "queue_review",
                "label": f"Queue {len(batches['human_review'])} case(s) for human review",
                "detail": "Assigned to Document Ops · Tier 2 with the reason and missing fields attached.",
                "job_ids": [c["job_id"] for c in batches["human_review"]],
                "vendors": sorted({c["vendor"] for c in batches["human_review"]}),
            }
        )
    if batches["escalate"]:
        segments.append(
            {
                "action_type": "escalate",
                "label": f"Escalate {len(batches['escalate'])} case(s) to the on-call lead",
                "detail": "Opens an incident with the policy citation and SLA breach attached.",
                "job_ids": [c["job_id"] for c in batches["escalate"]],
                "vendors": sorted({c["vendor"] for c in batches["escalate"]}),
            }
        )
    return segments


def draft_action(
    trace: Trace,
    *,
    session_id: str,
    batches: dict[str, list[dict]],
) -> dict | None:
    started = time.perf_counter()
    segments = _segments_from_batches(batches)
    if not segments:
        trace.timed("draft_action", {}, "nothing to execute", started)
        return None

    all_cases = [c for group in batches.values() for c in group]
    policies = sorted({p for c in all_cases for p in c["policies"]})
    retry_cases = batches["auto_retry"]
    high_value = [c["job_id"] for c in all_cases if c["amount_usd"] >= config.HUMAN_SIGNOFF_VALUE_USD]
    breached = [c["job_id"] for c in all_cases if c["sla_due_in_hours"] < 0]

    title = f"Execute {len(segments)}-part resolution plan across {len(all_cases)} document(s)"
    if retry_cases:
        title = f"Retry {len(retry_cases)} safe failure(s), route {len(all_cases) - len(retry_cases)} for human handling"

    risk_notes = []
    if retry_cases:
        risk_notes.append(
            f"Retry slice is {dataset.format_money(sum(c['amount_usd'] for c in retry_cases))} "
            f"across {len({c['vendor'] for c in retry_cases})} vendor(s); every document is below the "
            f"{dataset.format_money(config.AUTO_RETRY_VALUE_CEILING_USD)} per-document ceiling."
        )
    if high_value:
        n = len(high_value)
        risk_notes.append(f"{n} document{'s are' if n > 1 else ' is'} held for human sign-off, not automated.")
    if breached:
        n = len(breached)
        risk_notes.append(f"{n} SLA breach{'es are' if n > 1 else ' is'} escalated rather than retried.")

    action = {
        "action_id": f"act_{uuid.uuid4().hex[:10]}",
        "session_id": session_id,
        "title": title,
        "segments": segments,
        "policies": policies,
        "policy_names": [domain.POLICIES.get(p, {}).get("name", p) for p in policies],
        "risk_note": " ".join(risk_notes),
        "requires_approval": True,
    }

    db.save_pending_action(
        action_id=action["action_id"],
        session_id=session_id,
        title=title,
        segments=segments,
        policies=policies,
        risk_note=action["risk_note"],
    )

    trace.timed(
        "draft_action",
        {"segments": len(segments), "jobs": len(all_cases)},
        f"approval required · {len(policies)} polic{'y' if len(policies) == 1 else 'ies'} cited",
        started,
    )
    return action


# --- tool: memory recall ----------------------------------------------------


_STOPWORDS = {
    "what", "did", "we", "decide", "decided", "about", "the", "a", "an", "for", "on", "in", "to",
    "last", "time", "yesterday", "today", "remember", "recall", "history", "decision", "decisions",
    "me", "tell", "show", "any", "was", "were", "is", "are", "of", "and", "or", "that", "this",
    "have", "has", "do", "does", "you", "i", "our", "with", "from", "at", "by", "it",
}


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9\-]+", text.lower()) if len(t) > 1}


def recall_memory(trace: Trace, query: str, *, limit: int = 5) -> list[dict]:
    started = time.perf_counter()
    words = _tokens(query) - _STOPWORDS
    rows = db.all_decisions(limit=200)

    scored: list[tuple[float, dict]] = []
    for index, row in enumerate(rows):
        score = 0.0
        vendor_hits = {w for w in words if any(w in v.lower() for v in row["vendors"])}
        job_hits = {w for w in words if any(w in j.lower() for j in row["job_ids"])}
        text_blob = " ".join(row["action_types"] + row["policies"] + [row["title"], row["outcome"]]).lower()
        text_hits = {w for w in words if w in text_blob}
        score += 5.0 * len(vendor_hits)
        score += 3.0 * len(job_hits)
        score += 1.5 * len(text_hits)
        if not words:
            score = 0.1
        if score <= 0:
            continue
        score += max(0.0, 1.5 - index * 0.08)  # recency tiebreaker
        scored.append((score, row))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    hits = [row for _, row in scored[:limit]]

    trace.timed(
        "recall_memory",
        {"query": query, "terms": sorted(words)},
        f"{len(hits)} matching decision(s) from {len(rows)} stored",
        started,
    )
    return hits


# --- tool: execute (approval already granted) -------------------------------


REVIEW_ASSIGNEE = "Document Ops · Tier 2"
ESCALATION_OWNER = "On-call Ops Lead"


def execute_action(action: dict, *, actor: str = "operator") -> dict:
    """Apply an approved action to the live job board.

    Returns a summary of what changed. No approval check happens here — the
    caller is responsible for verifying the pending action is still approveable.
    """
    executed: list[dict] = []
    changed_jobs: list[str] = []

    for segment in action["segments"]:
        kind = segment["action_type"]
        job_ids = segment["job_ids"]

        if kind == "retry_jobs":
            for job_id in job_ids:
                job = dataset.get_job(job_id)
                if not job:
                    continue
                dataset.set_status(job_id, "retrying", attempts=int(job.get("attempts") or 0) + 1)
                db.add_audit(
                    event="retry_dispatched",
                    actor="agent",
                    session_id=action.get("session_id"),
                    detail=f"{job_id} re-dispatched under approval {action['action_id']}",
                    job_ids=[job_id],
                )
                changed_jobs.append(job_id)
            executed.append({"action_type": kind, "count": len(job_ids), "status": "retrying"})

        elif kind == "queue_review":
            for job_id in job_ids:
                job = dataset.get_job(job_id)
                if not job:
                    continue
                dataset.set_status(job_id, "queued_review", assignee=REVIEW_ASSIGNEE)
                db.add_audit(
                    event="review_queued",
                    actor="agent",
                    session_id=action.get("session_id"),
                    detail=f"{job_id} assigned to {REVIEW_ASSIGNEE}",
                    job_ids=[job_id],
                )
                changed_jobs.append(job_id)
            executed.append({"action_type": kind, "count": len(job_ids), "status": "queued_review"})

        elif kind == "escalate":
            for job_id in job_ids:
                job = dataset.get_job(job_id)
                if not job:
                    continue
                dataset.set_status(job_id, "escalated", assignee=ESCALATION_OWNER)
                db.add_audit(
                    event="escalated",
                    actor="agent",
                    session_id=action.get("session_id"),
                    detail=f"{job_id} escalated to {ESCALATION_OWNER}",
                    job_ids=[job_id],
                )
                changed_jobs.append(job_id)
            executed.append({"action_type": kind, "count": len(job_ids), "status": "escalated"})

    return {
        "action_id": action.get("action_id"),
        "executed": executed,
        "changed_jobs": changed_jobs,
        "actor": actor,
    }
