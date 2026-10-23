"""Domain model: failure taxonomy, the automation policy, and case classification.

This module is the reason the agent is trustworthy. Every recommendation is
produced by an explicit, auditable rule that cites a named policy. The LLM (when
enabled) is only allowed to rephrase the narration, never to pick the action.
"""

from __future__ import annotations

from . import config

FAILURES: dict[str, dict[str, str]] = {
    "LOW_OCR_CONFIDENCE": {
        "label": "Low OCR confidence",
        "cause": "The scanned source is skewed or low-DPI, so extracted fields fall below the confidence floor.",
        "remedy_auto": "Retry with the enhanced OCR profile",
        "remedy_human": "Re-key the document from the source PDF",
    },
    "UPSTREAM_TIMEOUT": {
        "label": "Upstream timeout",
        "cause": "The parsing service did not respond inside the job budget. The operation is idempotent.",
        "remedy_auto": "Re-dispatch the parse job",
        "remedy_human": "Re-dispatch manually after checking upstream health",
    },
    "SCHEMA_VALIDATION_ERROR": {
        "label": "Schema validation error",
        "cause": "Fields were extracted but do not satisfy the current invoice schema revision.",
        "remedy_auto": "Re-parse against the current schema revision",
        "remedy_human": "Correct the mapping template",
    },
    "MISSING_REQUIRED_FIELD": {
        "label": "Missing required field",
        "cause": "A mandatory field is absent from the source document and cannot be inferred safely.",
        "remedy_auto": "Cannot be automated",
        "remedy_human": "Request the missing field from the vendor",
    },
    "DUPLICATE_DOCUMENT": {
        "label": "Suspected duplicate",
        "cause": "The document hash matches a previously ingested document.",
        "remedy_auto": "Cannot be automated",
        "remedy_human": "Confirm and void one of the two records",
    },
    "VENDOR_MISMATCH": {
        "label": "Vendor master-data mismatch",
        "cause": "The vendor name on the document does not match the master-data record.",
        "remedy_auto": "Cannot be automated",
        "remedy_human": "Confirm the legal entity with Vendor Data",
    },
}

POLICIES: dict[str, dict[str, str]] = {
    "POLICY-RETRY-01": {
        "name": "Idempotent re-dispatch",
        "rule": "Upstream timeouts are idempotent and may be retried automatically after operator approval, up to 3 attempts.",
    },
    "POLICY-RETRY-02": {
        "name": "Schema re-parse",
        "rule": "Schema validation errors may be re-parsed automatically after operator approval while attempts remain under 3.",
    },
    "POLICY-OCR-05": {
        "name": "Bounded OCR retry",
        "rule": "OCR failures may retry automatically only when confidence >= 0.65, value < $25,000 and attempts < 3.",
    },
    "POLICY-OCR-06": {
        "name": "OCR human re-key",
        "rule": "OCR failures below 0.65 confidence, above $25,000, or already retried 3 times must be re-keyed by a human.",
    },
    "POLICY-FIELD-03": {
        "name": "No field inference",
        "rule": "Missing required fields cannot be inferred by the agent. Route to a reviewer for vendor outreach.",
    },
    "POLICY-DUP-01": {
        "name": "Duplicate confirmation",
        "rule": "Suspected duplicates require human confirmation before either document is voided.",
    },
    "POLICY-VENDOR-04": {
        "name": "Vendor master-data sign-off",
        "rule": "Vendor mismatches must be confirmed by the Vendor Data team before payment routing.",
    },
    "POLICY-RISK-07": {
        "name": "High-value sign-off",
        "rule": "Any document valued at or above $50,000 requires human sign-off regardless of failure type.",
    },
    "POLICY-SLA-08": {
        "name": "Enterprise SLA breach escalation",
        "rule": "A breached SLA on an enterprise account is escalated to the on-call operations lead.",
    },
}

RECOMMENDATION_LABELS = {
    "auto_retry": "Safe to retry",
    "human_review": "Human review",
    "escalate": "Escalate",
}

DOC_LABELS = {
    "invoice": "invoice",
    "kyc_form": "KYC form",
    "bill_of_lading": "bill of lading",
    "proof_of_delivery": "proof of delivery",
}


def doc_label(doc_type: str | None) -> str:
    if not doc_type:
        return "document"
    return DOC_LABELS.get(doc_type, doc_type.replace("_", " "))


def money(value: float | None) -> str:
    if not value:
        return "$0"
    return "${:,.0f}".format(value)


def _priority(score: int) -> str:
    if score >= 65:
        return "P0"
    if score >= 38:
        return "P1"
    return "P2"


def _risk_score(job: dict) -> int:
    amount = float(job.get("amount_usd") or 0)
    sla = float(job.get("sla_due_in_hours") or 0)
    score = 12.0
    score += min(35.0, amount / 50_000 * 35.0)
    if job.get("customer_tier") == "enterprise":
        score += 18
    if sla < 0:
        score += 14
    elif sla < config.SLA_CRITICAL_HOURS:
        score += 9
    elif sla < config.SLA_AT_RISK_HOURS:
        score += 4
    if int(job.get("attempts") or 0) >= config.MAX_AUTO_ATTEMPTS:
        score += 12
    if amount >= config.HUMAN_SIGNOFF_VALUE_USD:
        score += 8
    if job.get("doc_type") == "kyc_form":
        score += 6
    return int(min(100, round(score)))


def _decide(job: dict) -> tuple[str, list[str], str]:
    """Return (recommendation, policy_refs, rationale)."""
    code = job.get("failure_code") or ""
    amount = float(job.get("amount_usd") or 0)
    sla = float(job.get("sla_due_in_hours") or 0)
    attempts = int(job.get("attempts") or 0)
    tier = job.get("customer_tier")
    conf = job.get("ocr_confidence")
    meta = FAILURES.get(code, {"remedy_auto": "Retry", "remedy_human": "Review manually"})

    # 1. Vendor master-data integrity always outranks automation.
    if code == "VENDOR_MISMATCH":
        return (
            "escalate",
            ["POLICY-VENDOR-04"],
            f"Document names '{job.get('vendor_on_document', 'an unmatched entity')}' but master data holds "
            f"'{job.get('vendor')}'. Payment routing is blocked until Vendor Data confirms the legal entity.",
        )

    # 2. A breached SLA on an enterprise account is an incident, not a queue item.
    if sla < 0 and tier == "enterprise":
        return (
            "escalate",
            ["POLICY-SLA-08"],
            f"SLA was breached {abs(sla):.0f}h ago on an enterprise account after {attempts} attempt(s). "
            "On-call operations lead must own the recovery.",
        )

    # 3. Duplicates need a human decision because one record gets voided.
    if code == "DUPLICATE_DOCUMENT":
        return (
            "human_review",
            ["POLICY-DUP-01"],
            f"Document hash matches {job.get('duplicate_of', 'an earlier job')}. Voiding a record is not reversible, "
            "so a reviewer confirms which copy is authoritative.",
        )

    # 4. The agent will not invent data that is not on the page.
    if code == "MISSING_REQUIRED_FIELD":
        fields = ", ".join(job.get("missing_fields") or []) or "a required field"
        return (
            "human_review",
            ["POLICY-FIELD-03"],
            f"Missing: {fields}. The value does not exist in the source document, so automation would have to guess.",
        )

    # 5. Value ceiling overrides every retry rule.
    if amount >= config.HUMAN_SIGNOFF_VALUE_USD:
        return (
            "human_review",
            ["POLICY-RISK-07"],
            f"{money(amount)} exceeds the {money(config.HUMAN_SIGNOFF_VALUE_USD)} sign-off threshold. "
            "High-value documents get a human even when the failure is mechanically retryable.",
        )

    # 6. OCR: retry only inside a narrow, bounded box.
    if code == "LOW_OCR_CONFIDENCE":
        if conf is not None and conf >= 0.65 and attempts < config.MAX_AUTO_ATTEMPTS and amount < config.AUTO_RETRY_VALUE_CEILING_USD:
            return (
                "auto_retry",
                ["POLICY-OCR-05"],
                f"Confidence {conf:.2f} is inside the retryable band, {attempts} of {config.MAX_AUTO_ATTEMPTS} attempts used, "
                f"and {money(amount)} is under the {money(config.AUTO_RETRY_VALUE_CEILING_USD)} ceiling.",
            )
        return (
            "human_review",
            ["POLICY-OCR-06"],
            f"Confidence {conf if conf is not None else 0:.2f} with {attempts} attempt(s) falls outside the retryable band. "
            "Another automated pass would most likely fail the same way.",
        )

    # 7. Stateless, idempotent work is the safest thing to automate.
    if code == "UPSTREAM_TIMEOUT" and attempts < config.MAX_AUTO_ATTEMPTS:
        return (
            "auto_retry",
            ["POLICY-RETRY-01"],
            "Timeout is idempotent and the job is under the attempt limit, so a re-dispatch carries no data risk.",
        )

    if code == "SCHEMA_VALIDATION_ERROR" and attempts < config.MAX_AUTO_ATTEMPTS:
        return (
            "auto_retry",
            ["POLICY-RETRY-02"],
            f"Re-parsing against the current schema revision is safe and {attempts} of {config.MAX_AUTO_ATTEMPTS} attempts are used.",
        )

    return (
        "human_review",
        [],
        f"{meta['label']} has no approved automation path. Route to a reviewer.",
    )


def _sla_label(hours: float) -> tuple[str, str]:
    if hours < 0:
        return f"{abs(hours):.0f}h overdue", "critical"
    if hours < config.SLA_CRITICAL_HOURS:
        return f"{hours:.0f}h left", "critical"
    if hours < config.SLA_AT_RISK_HOURS:
        return f"{hours:.0f}h left", "warning"
    return f"{hours:.0f}h left", "ok"


def _received_label(hours: float) -> str:
    if hours < 1:
        return f"{hours * 60:.0f}m ago"
    if hours < 24:
        return f"{hours:.0f}h ago"
    return f"{hours / 24:.1f}d ago"


def build_case(job: dict) -> dict:
    """Turn a raw job row into an operator-facing case card."""
    code = job.get("failure_code") or ""
    status = job.get("status", "unknown")
    sla_hours = float(job.get("sla_due_in_hours") or 0)
    sla_label, sla_tone = _sla_label(sla_hours)

    if status == "pending_review":
        return {
            "job_id": job["job_id"],
            "vendor": job["vendor"],
            "doc_type": job.get("doc_type", "document"),
            "amount_usd": float(job.get("amount_usd") or 0),
            "amount_label": money(job.get("amount_usd")),
            "status": status,
            "status_label": "Pending review",
            "priority": _priority(_risk_score(job)),
            "risk_score": _risk_score(job),
            "failure_code": "",
            "failure_label": "Awaiting reviewer",
            "cause": "Already owned by a human reviewer. The agent will not duplicate the work.",
            "recommendation": "human_review",
            "recommendation_label": "Already in review",
            "remedy": "Continue the reviewer workflow",
            "rationale": "This document is in the human queue, so it is out of scope for automation.",
            "policies": [],
            "policy_names": [],
            "sla_due_in_hours": round(sla_hours, 1),
            "sla_label": sla_label,
            "sla_tone": sla_tone,
            "attempts": int(job.get("attempts") or 0),
            "ocr_confidence": job.get("ocr_confidence"),
            "missing_fields": [],
            "duplicate_of": None,
            "tier": job.get("customer_tier", "standard"),
            "received_label": _received_label(float(job.get("received_hours_ago") or 0)),
            "pages": job.get("pages"),
            "assignee": job.get("assignee"),
            "tone": "review",
        }

    meta = FAILURES.get(code, {"label": code or "Unclassified", "cause": "", "remedy_auto": "", "remedy_human": ""})
    recommendation, policies, rationale = _decide(job)
    score = _risk_score(job)
    remedy = meta["remedy_auto"] if recommendation == "auto_retry" else meta["remedy_human"]

    status_label = {
        "failed": "Failed",
        "retrying": "Retry dispatched",
        "recovered": "Recovered",
        "queued_review": "In review queue",
        "escalated": "Escalated",
        "completed": "Completed",
    }.get(status, str(status).title())

    if status == "failed":
        tone = {"auto_retry": "warn", "human_review": "review", "escalate": "danger"}[recommendation]
        display_recommendation = RECOMMENDATION_LABELS[recommendation]
    else:
        tone = {
            "retrying": "warn",
            "recovered": "ok",
            "queued_review": "review",
            "escalated": "danger",
        }.get(status, "muted")
        display_recommendation = status_label

    return {
        "job_id": job["job_id"],
        "vendor": job["vendor"],
        "doc_type": job.get("doc_type", "document"),
        "amount_usd": float(job.get("amount_usd") or 0),
        "amount_label": money(job.get("amount_usd")),
        "status": status,
        "status_label": status_label,
        "priority": _priority(score),
        "risk_score": score,
        "failure_code": code,
        "failure_label": meta["label"],
        "cause": meta["cause"],
        "recommendation": recommendation,
        "recommendation_label": display_recommendation,
        "remedy": remedy,
        "rationale": rationale,
        "policies": policies,
        "policy_names": [POLICIES.get(p, {}).get("name", p) for p in policies],
        "sla_due_in_hours": round(sla_hours, 1),
        "sla_label": sla_label,
        "sla_tone": sla_tone,
        "attempts": int(job.get("attempts") or 0),
        "ocr_confidence": job.get("ocr_confidence"),
        "missing_fields": job.get("missing_fields") or [],
        "duplicate_of": job.get("duplicate_of"),
        "tier": job.get("customer_tier", "standard"),
        "received_label": _received_label(float(job.get("received_hours_ago") or 0)),
        "pages": job.get("pages"),
        "assignee": job.get("assignee"),
        "tone": tone,
    }
