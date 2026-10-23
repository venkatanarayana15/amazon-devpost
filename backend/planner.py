"""Conversation planner: intent parsing, tool orchestration, and narration.

The planner is deliberately simple about language and precise about state.
Intent extraction is a small, readable ruleset; everything after that is an
explicit tool call over the synthetic dataset. The optional LLM only rewrites
the narration string and can never change a decision.

This module is intentionally ASCII-only so it is safe to edit on any platform.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime

from . import config, dataset, db, domain, llm, tools

RECALL_PATTERNS = (
    "what did we decide",
    "what did we do",
    "remember",
    "last time",
    "yesterday",
    "history",
    "decision",
    "decisions",
    "what happened",
    "recall",
    "previous",
)

RESOLVE_PATTERNS = (
    "resolve",
    "fix",
    "retry",
    "escalate",
    "handle",
    "clean up",
    "clear the",
    "dispatch",
    "queue",
    "route",
    "action",
    "do it",
    "proceed",
    "approve",
)

DIAGNOSE_PATTERNS = (
    "why",
    "failing",
    "failure",
    "failures",
    "diagnose",
    "status",
    "what's wrong",
    "whats wrong",
    "how many",
    "broken",
    "stuck",
    "blocking",
    "blocked",
    "overview",
    "summarise",
    "summarize",
)

HOLD_PATTERNS = ("hold everything", "manual triage", "do nothing", "not now", "cancel")

SCOPE_WORDS = (
    "safe",
    "low-risk",
    "low risk",
    "high-risk",
    "high risk",
    "escalate",
    "review",
    "all",
    "everything",
    "urgent",
    "p0",
    "p1",
    "auto",
    "automatic",
    "only",
)

DOC_TYPE_ALIASES = {
    "invoice": "invoice",
    "kyc": "kyc_form",
    "kyc form": "kyc_form",
    "bill of lading": "bill_of_lading",
    "lading": "bill_of_lading",
    "proof of delivery": "proof_of_delivery",
    "delivery": "proof_of_delivery",
}

OPEN_STATUSES = {"failed", "retrying", "queued_review", "escalated", "pending_review"}
BOARD_STATUSES = OPEN_STATUSES | {"recovered"}

STATUS_PHRASES = {
    "failed": "failed",
    "retrying": "retrying",
    "recovered": "recovered",
    "queued_review": "in the review queue",
    "escalated": "escalated",
    "pending_review": "with a reviewer",
    "completed": "completed",
}


# --- small text helpers -----------------------------------------------------


def _lc(label: str) -> str:
    """Lowercase the first letter only, so acronyms such as OCR survive."""
    return label[:1].lower() + label[1:] if label else label


def _plural(count: int, word: str, plural_form: str | None = None) -> str:
    if count == 1:
        return f"{count} {word}"
    return f"{count} {plural_form or word + 's'}"


def _money(value: float) -> str:
    return dataset.format_money(value)


def _fmt_ts(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    if dt.tzinfo is not None:
        dt = dt.astimezone()
    return dt.strftime("%d %b, %H:%M")


def _group_phrase(cases: list[dict], limit: int = 3) -> str:
    counts = Counter(c["failure_label"] for c in cases)
    parts = [f"{n} x {_lc(label)}" if n > 1 else _lc(label) for label, n in counts.most_common(limit)]
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return ", ".join(parts[:-1]) + f" and {parts[-1]}"


def _scope_noun(vendor: str | None, doc_type: str | None, count: int) -> str:
    doc = domain.doc_label(doc_type) if doc_type else ""
    if vendor and doc:
        base = f"{doc} documents from {vendor}"
    elif vendor:
        base = f"documents from {vendor}"
    elif doc:
        base = f"{doc} documents"
    else:
        base = "documents"
    if count == 1:
        return base.replace("documents", "document")
    return base


def _scope_phrase(vendor: str | None, doc_type: str | None) -> str:
    doc = domain.doc_label(doc_type) if doc_type else ""
    if vendor and doc:
        return f"{vendor} {doc} documents"
    if vendor:
        return vendor
    if doc:
        return f"the {doc} queue"
    return "the queue"


# --- intent -----------------------------------------------------------------

OPS_WORDS = {
    "job", "jobs", "document", "documents", "doc", "docs", "invoice", "invoices",
    "queue", "vendor", "vendors", "failure", "failures", "failing", "failed",
    "retry", "retries", "retrying", "escalate", "escalation", "review", "policy",
    "policies", "sla", "ocr", "extract", "extraction", "approve", "approval",
    "hold", "board", "case", "cases", "receipt", "receipts", "purchase", "order",
    "onboarding", "compliance", "commander", "docops", "batch", "batches",
}


def _has_ops_vocabulary(lower: str) -> bool:
    return any(re.search(rf"\b{re.escape(word)}\b", lower) for word in OPS_WORDS)


def parse_intent(text: str) -> dict:
    lower = text.lower().strip()

    if any(p in lower for p in HOLD_PATTERNS):
        intent = "hold"
    elif any(p in lower for p in RECALL_PATTERNS):
        intent = "recall"
    elif any(p in lower for p in RESOLVE_PATTERNS) and not lower.startswith("why"):
        intent = "resolve"
    elif any(p in lower for p in DIAGNOSE_PATTERNS):
        intent = "diagnose"
    elif lower in {"help", "what can you do", "commands"}:
        intent = "help"
    elif _has_ops_vocabulary(lower):
        intent = "diagnose"
    else:
        # Outside the document-operations vocabulary: search memory rather than
        # inventing an operations answer.
        intent = "recall"

    vendor = None
    for candidate in dataset.vendors():
        if candidate.lower() in lower:
            vendor = candidate
            break
    if vendor is None:
        for candidate in dataset.vendors():
            first = candidate.split()[0].lower()
            if re.search(rf"\b{re.escape(first)}\b", lower):
                vendor = candidate
                break

    doc_type = None
    for alias, canonical in DOC_TYPE_ALIASES.items():
        if alias in lower:
            doc_type = canonical
            break

    return {
        "intent": intent,
        "vendor": vendor,
        "doc_type": doc_type,
        "scoped": any(word in lower for word in SCOPE_WORDS),
        "has_job_id": bool(re.search(r"job-\d+", lower)),
        "raw": text,
    }


# --- narration --------------------------------------------------------------


def _diagnose_reply(cases: list[dict], vendor: str | None = None, doc_type: str | None = None) -> str:
    if not cases:
        return _no_cases_reply(vendor, doc_type)

    total = sum(c["amount_usd"] for c in cases)
    breach = [c for c in cases if c["sla_due_in_hours"] < 0]
    soon = [c for c in cases if 0 <= c["sla_due_in_hours"] < config.SLA_AT_RISK_HOURS]
    auto = [c for c in cases if c["recommendation"] == "auto_retry"]
    counts = Counter(c["failure_code"] for c in cases)
    top_codes = [code for code, _ in counts.most_common(3)]
    pattern_bits = [
        f"{_lc(domain.FAILURES.get(code, {'label': code})['label'])} ({counts[code]})" for code in top_codes
    ]

    if len(pattern_bits) == 1:
        pattern_sentence = f"One pattern explains it: {pattern_bits[0]}."
    elif len(pattern_bits) == 2:
        pattern_sentence = f"Two patterns explain it: {pattern_bits[0]} and {pattern_bits[1]}."
    else:
        pattern_sentence = (
            f"The largest patterns are {pattern_bits[0]}, {pattern_bits[1]} and {pattern_bits[2]}."
        )

    noun = _scope_noun(vendor, doc_type, len(cases))
    verb = "is" if len(cases) == 1 else "are"
    lines = [
        f"{len(cases)} {noun} {verb} failing right now, carrying "
        f"{_money(total)} at risk.",
        pattern_sentence,
    ]

    sla_bits = []
    if soon:
        sla_bits.append(f"{len(soon)} inside a {config.SLA_AT_RISK_HOURS}-hour SLA window")
    if breach:
        sla_bits.append(f"{len(breach)} already breached")
    if sla_bits:
        lines.append("SLA pressure: " + " and ".join(sla_bits) + ".")

    worst = max(cases, key=lambda c: (c["risk_score"], -c["sla_due_in_hours"]))
    lines.append(
        f"Start with {worst['job_id']}: {worst['vendor']}, {worst['amount_label']}, "
        f"{worst['sla_label']}, {_lc(worst['failure_label'])}. "
        f"{worst['recommendation_label']} under {', '.join(worst['policies']) or 'operator judgement'}."
    )
    if auto:
        lines.append(
            f"{len(auto)} of these match an approved automation policy. "
            "Say the word and I will draft the plan for your approval."
        )
    return " ".join(lines)


def _no_cases_reply(vendor: str | None, doc_type: str | None) -> str:
    matching = [
        job
        for job in dataset.all_jobs()
        if (not vendor or job["vendor"] == vendor) and (not doc_type or job["doc_type"] == doc_type)
    ]
    if not matching:
        return (
            "I have no documents matching that scope in the feed. Try a vendor name, a document type "
            "such as invoice or KYC form, or ask me for a board overview."
        )

    live = [job for job in matching if job["status"] != "completed"]
    phrase = _scope_phrase(vendor, doc_type)
    if not live:
        total = len(matching)
        return (
            f"Nothing is failing for {phrase}. All {_plural(total, 'document')} in scope "
            f"{'is' if total == 1 else 'are'} completed. Nothing needs an action from me."
        )

    bits = []
    for job in live[:4]:
        status = STATUS_PHRASES.get(job["status"], job["status"])
        amount = f", {dataset.format_money(job['amount_usd'])}" if job.get("amount_usd") else ""
        bits.append(f"{job['job_id']} is {status}{amount}")
    more = f" and {len(live) - 4} more" if len(live) > 4 else ""
    return (
        f"Nothing is failing for {phrase} right now. " + "; ".join(bits) + more +
        ". Those are already moving or owned, so there is no action for me to take."
    )


def _resolve_reply(batches: dict[str, list[dict]], action: dict | None) -> str:
    retry = batches["auto_retry"]
    review = batches["human_review"]
    escalate = batches["escalate"]
    total = len(retry) + len(review) + len(escalate)

    if total == 0:
        return "There is nothing failing that I can act on. The board is already clear."

    lines = []
    if retry:
        value = sum(c["amount_usd"] for c in retry)
        one = len(retry) == 1
        lines.append(
            f"{_plural(len(retry), 'case')} {'matches' if one else 'match'} an approved retry policy and "
            f"{'involves' if one else 'involve'} no human judgement: {_money(value)} of exposure, with every "
            f"document under the {_money(config.AUTO_RETRY_VALUE_CEILING_USD)} per-document ceiling."
        )
    if review:
        lines.append(f"{len(review)} need a person ({_group_phrase(review)}).")
    if escalate:
        lines.append(f"{len(escalate)} escalate to the on-call lead ({_group_phrase(escalate)}).")

    body = " ".join(lines)

    if action is None:
        return body

    tail = (
        f"That is a {len(action['segments'])}-part plan citing {len(action['policies'])} "
        f"polic{'y' if len(action['policies']) == 1 else 'ies'}. Nothing runs until you approve it; "
        "the approval gate is the only way an action reaches the board."
    )
    return f"{body} {tail}"


def _recall_reply(hits: list[dict], query: str, vendor: str | None, total_decisions: int = 0) -> str:
    """Generate a recall reply, searching the full decision database if no in-memory hits."""
    # If we have in-memory hits from the current session, use them
    if hits:
        top = hits[0]
        detail = top.get("detail") or {}
        job_ids = top["job_ids"]
        if vendor:
            scoped = [jid for jid in job_ids if (dataset.get_job(jid) or {}).get("vendor") == vendor]
            job_ids = scoped or job_ids
        jobs = ", ".join(job_ids[:6])
        more = f" (+{len(job_ids) - 6} more)" if len(job_ids) > 6 else ""
        policies = ", ".join(top["policies"]) or "no explicit policy"
        outcome = top["outcome"]
        title = _lc(top["title"])

        if outcome == "approved":
            result = detail.get("result") or "executed"
            opening = f"On {_fmt_ts(top['created_at'])} you approved: {title}. Outcome: {result}."
        elif outcome == "rejected":
            opening = f"On {_fmt_ts(top['created_at'])} you rejected: {title}. Nothing was executed."
        elif outcome == "held":
            opening = (
                f"On {_fmt_ts(top['created_at'])} you chose to hold the board for manual triage "
                "instead of automating it."
            )
        else:
            opening = (
                f"On {_fmt_ts(top['created_at'])} I proposed: {title}. It is still awaiting your approval."
            )

        parts = [opening, f"Policy applied: {policies}.", f"Jobs: {jobs}{more}."]

        if vendor:
            open_now = [c for c in _open_cases() if c["vendor"] == vendor]
            if open_now:
                parts.append(
                    f"{vendor} has {_plural(len(open_now), 'open case')} right now "
                    f"({', '.join(c['job_id'] for c in open_now[:4])}), so that precedent applies directly."
                )
            elif job_ids:
                parts.append(f"{vendor} has no open cases on the board right now.")

        if len(hits) > 1:
            extra = len(hits) - 1
            singular = extra == 1
            parts.append(
                f"{_plural(extra, 'earlier decision')} {'matches' if singular else 'match'} the same query "
                f"and {'is' if singular else 'are'} listed in the memory panel."
            )
        return " ".join(parts)

    # No in-memory hits — try a direct database search with relaxed matching.
    all_rows = db.all_decisions(limit=500)
    if not all_rows:
        if total_decisions:
            return (
                f"I have {_plural(total_decisions, 'decision')} on file but none match that phrasing. "
                "Name a vendor, a job id, or ask about the last approved plan and I will pull the exact record."
            )
        scope = f" for {vendor}" if vendor else ""
        return (
            f"I have no stored decision{scope}. Decisions are written to SQLite the moment you approve or "
            "reject a plan, so an empty result means nothing has been actioned yet in this workspace."
        )

    qlower = query.lower()
    # Simple keyword matching as a fallback: look for vendor names, job ids, or key phrases.
    matched: list[dict] = []
    for row in all_rows:
        row_text = " ".join(
            [str(row.get("title", ""))] + row.get("policies", []) + [str(row.get("outcome", ""))]
        ).lower()
        # Vendor name match
        vendor_match = any(
            vw.lower() in qlower or qlower in vw.lower()
            for vw in row.get("vendors", [])
        )
        # Key phrase match (e.g. "retry", "hold", "approved")
        phrase_match = any(
            kw in qlower or qlower in kw
            for kw in ["retry", "hold", "approved", "decision", "orion", "vendor"]
        )
        if vendor_match or phrase_match:
            matched.append(row)

    if not matched:
        # Never answer an unrelated question with the nearest decision.
        if total_decisions:
            return (
                f"I have {_plural(total_decisions, 'decision')} on file but none match that phrasing. "
                "Name a vendor, a job id, or ask about the last approved plan and I will pull the exact record."
            )
        return (
            "I have no stored decision matching that phrasing. Name a vendor, a job id, "
            "or ask about the last approved plan and I will pull the exact record."
        )

    top = matched[0]
    detail = top.get("detail") or {}
    job_ids = top["job_ids"]
    if vendor:
        scoped = [jid for jid in job_ids if (dataset.get_job(jid) or {}).get("vendor") == vendor]
        job_ids = scoped or job_ids
    jobs = ", ".join(job_ids[:6])
    more = f" (+{len(job_ids) - 6} more)" if len(job_ids) > 6 else ""
    policies = ", ".join(top["policies"]) or "no explicit policy"
    outcome = top["outcome"]
    title = _lc(top["title"])

    if outcome == "approved":
        result = detail.get("result") or "executed"
        opening = f"On {_fmt_ts(top['created_at'])} you approved: {title}. Outcome: {result}."
    elif outcome == "rejected":
        opening = f"On {_fmt_ts(top['created_at'])} you rejected: {title}. Nothing was executed."
    elif outcome == "held":
        opening = (
            f"On {_fmt_ts(top['created_at'])} you chose to hold the board for manual triage "
            "instead of automating it."
        )
    else:
        opening = (
            f"On {_fmt_ts(top['created_at'])} I proposed: {title}. It is still awaiting your approval."
        )

    parts = [opening, f"Policy applied: {policies}.", f"Jobs: {jobs}{more}."]

    if vendor:
        open_now = [c for c in _open_cases() if c["vendor"] == vendor]
        if open_now:
            parts.append(
                f"{vendor} has {_plural(len(open_now), 'open case')} right now "
                f"({', '.join(c['job_id'] for c in open_now[:4])}), so that precedent applies directly."
            )
        elif job_ids:
            parts.append(f"{vendor} has no open cases on the board right now.")

    if len(matched) > 1:
        extra = len(matched) - 1
        singular = extra == 1
        parts.append(
            f"{_plural(extra, 'earlier decision')} {'matches' if singular else 'match'} the same query "
            f"and {'is' if singular else 'are'} listed in the memory panel."
        )
    return " ".join(parts)


def _proactive_next_steps(cases: list[dict], vendor: str | None = None, doc_type: str | None = None) -> str:
    """Suggest next actions after a diagnosis, based on the failure patterns found."""
    if not cases:
        return ""

    auto = [c for c in cases if c["recommendation"] == "auto_retry"]
    review = [c for c in cases if c["recommendation"] == "human_review"]
    escalate = [c for c in cases if c["recommendation"] == "escalate"]

    suggestions: list[str] = []

    if auto:
        suggestions.append(
            f"▶ {_plural(len(auto), 'auto-retry case')} are safe to dispatch immediately "
            f"(under the ${config.AUTO_RETRY_VALUE_CEILING_USD} per-document ceiling)."
        )

    if review:
        suggestions.append(
            f"⚙ {_plural(len(review), 'review case')} need a human reviewer — they "
            "can be queued now so the team can assess root cause."
        )

    if escalate:
        suggestions.append(
            f"⚠ {_plural(len(escalate), 'escalate case')} require on-call ownership; "
            "these appear on the board under the escalated badge."
        )

    if not suggestions:
        return ""

    header = "Based on the failure patterns, here are recommended next steps:"
    return header + " " + " ".join(suggestions)


def _help_reply() -> str:
    return (
        "I'm an Alexa+ style operations agent for document processing. Try: \"Why are today's invoice "
        "jobs failing?\" for a prioritised diagnosis; \"Resolve safe failures and escalate high-risk "
        "documents\" for an approval-gated plan; or \"What did we decide about Vendor Orion?\" to pull a "
        "past decision out of memory. I never execute anything without an explicit approval."
    )


# --- state ------------------------------------------------------------------


def _open_jobs() -> list[dict]:
    return [j for j in dataset.all_jobs() if j["status"] in OPEN_STATUSES]


def _open_cases() -> list[dict]:
    return sorted(
        (domain.build_case(j) for j in _open_jobs()),
        key=lambda c: (-c["risk_score"], c["sla_due_in_hours"]),
    )


def _board_cases() -> list[dict]:
    return sorted(
        (domain.build_case(j) for j in dataset.all_jobs() if j["status"] in BOARD_STATUSES),
        key=lambda c: (-c["risk_score"], c["sla_due_in_hours"]),
    )


def build_stats(jobs: list[dict] | None = None) -> dict:
    jobs = jobs if jobs is not None else dataset.all_jobs()
    failed = [j for j in jobs if j["status"] == "failed"]
    retrying = [j for j in jobs if j["status"] == "retrying"]
    recovered = [j for j in jobs if j["status"] == "recovered"]
    queued = [j for j in jobs if j["status"] == "queued_review"]
    escalated = [j for j in jobs if j["status"] == "escalated"]

    cases = [domain.build_case(j) for j in failed]
    auto = [c for c in cases if c["recommendation"] == "auto_retry"]
    p0 = [c for c in cases if c["priority"] == "P0"]
    value = sum(c["amount_usd"] for c in cases)
    sla_risk = [c for c in cases if c["sla_due_in_hours"] < config.SLA_AT_RISK_HOURS]
    breach = [c for c in cases if c["sla_due_in_hours"] < 0]
    handled = len(retrying) + len(recovered) + len(queued) + len(escalated)

    return {
        "failed": len(failed),
        "value_at_risk": value,
        "value_at_risk_label": _money(value),
        "sla_at_risk": len(sla_risk),
        "sla_breached": len(breach),
        "auto_eligible": len(auto),
        "p0": len(p0),
        "retrying": len(retrying),
        "recovered": len(recovered),
        "queued_review": len(queued),
        "escalated": len(escalated),
        "handled": handled,
        "tiles": [
            {
                "label": "Failing now",
                "value": str(len(failed)),
                "foot": f"{len(p0)} P0, {len(auto)} auto-eligible",
                "tone": "danger" if failed else "ok",
            },
            {
                "label": "Value at risk",
                "value": _money(value),
                "foot": f"{len(breach)} SLA breach" if breach else "no breaches",
                "tone": "warn" if value else "ok",
            },
            {
                "label": f"SLA < {config.SLA_AT_RISK_HOURS}h",
                "value": str(len(sla_risk)),
                "foot": f"{len(breach)} overdue",
                "tone": "danger" if breach else ("warn" if sla_risk else "ok"),
            },
            {
                "label": "Handled",
                "value": str(handled),
                "foot": f"{len(recovered)} recovered, {len(queued)} review, {len(escalated)} escalated",
                "tone": "info",
            },
        ],
    }


def state_payload() -> dict:
    return {
        "cards": _board_cases(),
        "stats": build_stats(),
        "memory": db.recent_decisions(limit=8),
        "audit": db.recent_audit(limit=20),
    }


# --- main entry point -------------------------------------------------------


def handle_command(session_id: str, text: str) -> dict:
    db.touch_session(session_id)
    db.add_message(session_id, "user", text)

    parsed = parse_intent(text)
    intent = parsed["intent"]
    trace = tools.Trace()

    if intent == "help":
        return _finish(session_id, intent, _help_reply(), trace, options=_suggestions())

    if intent == "recall":
        hits = tools.recall_memory(trace, text)
        reply = _recall_reply(
            hits, text, parsed["vendor"], total_decisions=len(db.all_decisions(limit=500))
        )
        db.add_audit(
            event="memory_recalled",
            actor="agent",
            session_id=session_id,
            detail=f"{len(hits)} decision(s) returned for a memory query",
        )
        payload = state_payload()
        reply, narrator = llm.polish(reply, {"intent": "recall", "hits": len(hits)})
        return _finish(
            session_id,
            intent,
            reply,
            trace,
            cards=payload["cards"],
            stats=payload["stats"],
            memory=hits,
            narrator=narrator,
        )

    # Shared diagnose-and-plan run.
    jobs, cases = tools.diagnose_jobs(trace, vendor=parsed["vendor"], doc_type=parsed["doc_type"])

    if not cases:
        reply, narrator = llm.polish(
            _no_cases_reply(parsed["vendor"], parsed["doc_type"]),
            {"intent": intent, "cases": 0},
        )
        payload = state_payload()
        return _finish(
            session_id,
            intent,
            reply,
            trace,
            cards=payload["cards"],
            stats=payload["stats"],
            narrator=narrator,
        )

    ranked = tools.prioritise(trace, cases)

    if intent == "diagnose":
        reply, narrator = llm.polish(
            _diagnose_reply(ranked, parsed["vendor"], parsed["doc_type"]),
            {"intent": "diagnose", "cases": len(ranked)},
        )
        payload = state_payload()
        return _finish(
            session_id,
            intent,
            reply,
            trace,
            cards=payload["cards"],
            stats=payload["stats"],
            narrator=narrator,
            priority="focus",
            focus=[c["job_id"] for c in ranked[:4]],
        )

    if intent == "hold":
        db.record_decision(
            session_id=session_id,
            title=f"Held {len(ranked)} failing document(s) for manual triage",
            action_types=["hold"],
            job_ids=[c["job_id"] for c in ranked],
            vendors=[c["vendor"] for c in ranked],
            policies=[],
            outcome="held",
            detail={"reason": "operator chose manual triage"},
        )
        db.add_audit(
            event="plan_held",
            actor="operator",
            session_id=session_id,
            detail=f"{len(ranked)} case(s) held for manual triage",
            job_ids=[c["job_id"] for c in ranked],
        )
        reply = (
            "Holding. Nothing was executed and nothing moved on the board. I logged the hold as a decision, "
            "so if you ask me later what happened to these documents I will tell you a person chose to "
            "triage them manually."
        )
        payload = state_payload()
        return _finish(
            session_id,
            "hold",
            reply,
            trace,
            cards=payload["cards"],
            stats=payload["stats"],
            memory=db.recent_decisions(limit=4),
        )

    # resolve
    batches = tools.build_batches(trace, ranked)
    ambiguous = (
        not parsed["scoped"]
        and not parsed["has_job_id"]
        and not parsed["vendor"]
        and batches["auto_retry"]
        and batches["human_review"]
    )

    if ambiguous:
        reply = (
            f"I found {_plural(len(batches['auto_retry']), 'case')} that match an approved retry policy and "
            f"{len(batches['human_review']) + len(batches['escalate'])} that need a person. Your policy allows "
            "either shape, so I need one decision from you before I draft anything: automate the safe slice, "
            "or hold the whole board for manual triage?"
        )
        payload = state_payload()
        return _finish(
            session_id,
            "clarify",
            reply,
            trace,
            cards=payload["cards"],
            stats=payload["stats"],
            options=[
                {
                    "id": "a",
                    "label": f"Retry the {len(batches['auto_retry'])} safe ones, route the rest",
                    "send": "Yes - retry the safe ones and route the rest for human review",
                    "tone": "primary",
                },
                {
                    "id": "b",
                    "label": "Hold everything for manual triage",
                    "send": "Hold everything for manual triage",
                    "tone": "ghost",
                },
            ],
        )

    action = tools.draft_action(trace, session_id=session_id, batches=batches)
    reply = _resolve_reply(batches, action)
    reply, narrator = llm.polish(
        reply,
        {
            "intent": "resolve",
            "retry": len(batches["auto_retry"]),
            "review": len(batches["human_review"]),
            "escalate": len(batches["escalate"]),
        },
    )

    if action:
        all_cases = [c for group in batches.values() for c in group]
        db.record_decision(
            session_id=session_id,
            action_id=action["action_id"],
            title=action["title"],
            action_types=[s["action_type"] for s in action["segments"]],
            job_ids=[c["job_id"] for c in all_cases],
            vendors=[c["vendor"] for c in all_cases],
            policies=action["policies"],
            outcome="pending",
            detail={"risk_note": action["risk_note"]},
        )
        db.add_audit(
            event="plan_proposed",
            actor="agent",
            session_id=session_id,
            detail=action["title"],
            job_ids=[c["job_id"] for c in all_cases],
        )

    payload = state_payload()
    return _finish(
        session_id,
        "resolve",
        reply,
        trace,
        cards=payload["cards"],
        stats=payload["stats"],
        pending_action=action,
        narrator=narrator,
        priority="focus",
        focus=[c["job_id"] for c in ranked[:6]],
    )


def _suggestions(intent: str | None = None) -> list[dict]:
    options = [
        {
            "id": "s1",
            "label": "Why are today's invoices failing?",
            "send": "Why are today's invoice jobs failing?",
            "tone": "ghost",
        },
        {
            "id": "s2",
            "label": "Resolve safe failures",
            "send": "Resolve safe failures and escalate high-risk documents",
            "tone": "ghost",
        },
        {
            "id": "s3",
            "label": "Recall Vendor Orion decision",
            "send": "What did we decide about Vendor Orion?",
            "tone": "ghost",
        },
    ]
    asked = {
        "diagnose": "Why are today's invoice jobs failing?",
        "resolve": "Resolve safe failures and escalate high-risk documents",
        "recall": "What did we decide about Vendor Orion?",
    }.get(intent or "")
    remaining = [option for option in options if option["send"] != asked]
    return remaining or options


def _finish(
    session_id: str,
    intent: str,
    reply: str,
    trace: tools.Trace,
    *,
    cards: list[dict] | None = None,
    stats: dict | None = None,
    pending_action: dict | None = None,
    options: list[dict] | None = None,
    memory: list[dict] | None = None,
    narrator: str = "deterministic",
    priority: str | None = None,
    focus: list[str] | None = None,
) -> dict:
    db.add_message(session_id, "agent", reply, intent)
    payload = state_payload()
    return {
        "session_id": session_id,
        "intent": intent,
        "reply": reply,
        "cards": cards if cards is not None else payload["cards"],
        "stats": stats if stats is not None else payload["stats"],
        "trace": trace.steps,
        "trace_ms": round(sum(step["ms"] for step in trace.steps), 2),
        "pending_action": pending_action,
        "options": options or _suggestions(intent),
        "memory": memory if memory is not None else payload["memory"],
        "audit": payload["audit"],
        "narrator": narrator,
        "priority": priority,
        "focus": focus or [],
    }
