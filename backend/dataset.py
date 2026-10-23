"""Synthetic document-operations feed.

The JSON file is the immutable seed. Live status changes (retry dispatched,
recovered, escalated) live in a small SQLite overlay so a page refresh, a second
browser tab, or a server restart all see the same board.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone

from . import config, db

_SEED_CACHE: list[dict] | None = None


def _load_seed() -> list[dict]:
    global _SEED_CACHE
    if _SEED_CACHE is None:
        raw = json.loads(config.DATA_FILE.read_text(encoding="utf-8"))
        _SEED_CACHE = raw["jobs"]
    return _SEED_CACHE


def _materialise(job: dict, now: datetime) -> dict:
    received = now - timedelta(hours=float(job.get("received_hours_ago") or 0))
    sla_due = now + timedelta(hours=float(job.get("sla_due_in_hours") or 0))
    out = dict(job)
    out["received_at"] = received.isoformat(timespec="minutes")
    out["sla_due_at"] = sla_due.isoformat(timespec="minutes")
    return out


def all_jobs() -> list[dict]:
    """Seed jobs with the persisted status overlay applied."""
    now = datetime.now(timezone.utc)
    states = db.load_job_states()
    jobs = []
    for seed in _load_seed():
        job = _materialise(seed, now)
        state = states.get(job["job_id"])
        if state:
            job["status"] = state["status"]
            job["attempts"] = max(int(job.get("attempts") or 0), int(state.get("attempts") or 0))
            job["assignee"] = state.get("assignee")
        jobs.append(job)
    return jobs


def get_job(job_id: str) -> dict | None:
    for job in all_jobs():
        if job["job_id"] == job_id:
            return job
    return None


def set_status(job_id: str, status: str, *, attempts: int | None = None, assignee: str | None = None) -> None:
    current = get_job(job_id)
    if current is None:
        return
    db.save_job_state(
        job_id,
        status,
        int(current.get("attempts") or 0) if attempts is None else attempts,
        assignee,
    )


def vendors() -> list[str]:
    return sorted({j["vendor"] for j in _load_seed()})


def doc_types() -> list[str]:
    return sorted({j["doc_type"] for j in _load_seed()})


def format_money(value: float | None) -> str:
    return "${:,.0f}".format(value or 0)


def format_hours(hours: float) -> str:
    if abs(hours) < 1:
        return f"{math.copysign(round(hours * 60), hours):+.0f}m"
    return f"{hours:+.1f}h"
