"""SQLite persistence for sessions, decisions, pending actions and audit events.

Deliberately dependency-free (stdlib sqlite3). A single connection guarded by a
lock is more than enough for a single-process demo and keeps the run story to
one command.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Iterable

from . import config

_LOCK = threading.RLock()
_CONN: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    last_seen   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    intent      TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decisions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    TEXT NOT NULL,
    action_id     TEXT,
    title         TEXT NOT NULL,
    action_types  TEXT NOT NULL DEFAULT '[]',
    job_ids       TEXT NOT NULL DEFAULT '[]',
    vendors       TEXT NOT NULL DEFAULT '[]',
    policies      TEXT NOT NULL DEFAULT '[]',
    outcome       TEXT NOT NULL DEFAULT 'pending',
    detail        TEXT NOT NULL DEFAULT '{}',
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pending_actions (
    action_id    TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL,
    title        TEXT NOT NULL,
    segments     TEXT NOT NULL,
    policies     TEXT NOT NULL DEFAULT '[]',
    risk_note    TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'pending',
    created_at   TEXT NOT NULL,
    resolved_at  TEXT
);

CREATE TABLE IF NOT EXISTS audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT,
    actor       TEXT NOT NULL,
    event       TEXT NOT NULL,
    detail      TEXT NOT NULL DEFAULT '',
    job_ids     TEXT NOT NULL DEFAULT '[]',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_state (
    job_id      TEXT PRIMARY KEY,
    status      TEXT NOT NULL,
    attempts    INTEGER NOT NULL DEFAULT 0,
    assignee    TEXT,
    updated_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_decisions_session ON decisions(session_id);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_audit_session ON audit(session_id);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_conn() -> sqlite3.Connection:
    global _CONN
    if _CONN is None:
        config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        conn.commit()
        _CONN = conn
    return _CONN


def execute(sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
    with _LOCK:
        conn = get_conn()
        cur = conn.execute(sql, tuple(params))
        conn.commit()
        return cur


def query(sql: str, params: Iterable[Any] = ()) -> list[dict]:
    with _LOCK:
        conn = get_conn()
        rows = conn.execute(sql, tuple(params)).fetchall()
        return [dict(r) for r in rows]


def _loads(raw: Any, fallback: Any) -> Any:
    if raw in (None, ""):
        return fallback
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return fallback


# --- sessions ---------------------------------------------------------------


def touch_session(session_id: str) -> None:
    now = utcnow()
    with _LOCK:
        conn = get_conn()
        conn.execute(
            "INSERT INTO sessions(id, created_at, last_seen) VALUES (?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen",
            (session_id, now, now),
        )
        conn.commit()


# --- messages ---------------------------------------------------------------


def add_message(session_id: str, role: str, content: str, intent: str | None = None) -> int:
    cur = execute(
        "INSERT INTO messages(session_id, role, content, intent, created_at) VALUES (?,?,?,?,?)",
        (session_id, role, content, intent, utcnow()),
    )
    return int(cur.lastrowid)


def recent_messages(session_id: str, limit: int = 40) -> list[dict]:
    return query(
        "SELECT * FROM messages WHERE session_id=? ORDER BY id DESC LIMIT ?",
        (session_id, limit),
    )[::-1]


# --- decisions --------------------------------------------------------------


def record_decision(
    *,
    session_id: str,
    title: str,
    action_types: list[str],
    job_ids: list[str],
    vendors: list[str],
    policies: list[str],
    outcome: str,
    detail: dict,
    action_id: str | None = None,
) -> int:
    cur = execute(
        "INSERT INTO decisions(session_id, action_id, title, action_types, job_ids, vendors, "
        "policies, outcome, detail, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            session_id,
            action_id,
            title,
            json.dumps(action_types),
            json.dumps(job_ids),
            json.dumps(sorted(set(vendors))),
            json.dumps(sorted(set(policies))),
            outcome,
            json.dumps(detail),
            utcnow(),
        ),
    )
    return int(cur.lastrowid)


def set_decision_outcome(action_id: str, outcome: str, detail: dict) -> None:
    execute(
        "UPDATE decisions SET outcome=?, detail=? WHERE action_id=? AND outcome='pending'",
        (outcome, json.dumps(detail), action_id),
    )


def recent_decisions(limit: int = 20) -> list[dict]:
    rows = query("SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (limit,))
    out = []
    for r in rows:
        r["action_types"] = _loads(r.get("action_types"), [])
        r["job_ids"] = _loads(r.get("job_ids"), [])
        r["vendors"] = _loads(r.get("vendors"), [])
        r["policies"] = _loads(r.get("policies"), [])
        r["detail"] = _loads(r.get("detail"), {})
        out.append(r)
    return out


def all_decisions(limit: int = 200) -> list[dict]:
    rows = query("SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (limit,))
    out = []
    for r in rows:
        r["action_types"] = _loads(r.get("action_types"), [])
        r["job_ids"] = _loads(r.get("job_ids"), [])
        r["vendors"] = _loads(r.get("vendors"), [])
        r["policies"] = _loads(r.get("policies"), [])
        r["detail"] = _loads(r.get("detail"), {})
        out.append(r)
    return out


# --- pending actions --------------------------------------------------------


def save_pending_action(
    *,
    action_id: str,
    session_id: str,
    title: str,
    segments: list[dict],
    policies: list[str],
    risk_note: str,
) -> None:
    execute(
        "INSERT INTO pending_actions(action_id, session_id, title, segments, policies, risk_note, "
        "status, created_at) VALUES (?,?,?,?,?,?, 'pending', ?)",
        (
            action_id,
            session_id,
            title,
            json.dumps(segments),
            json.dumps(sorted(set(policies))),
            risk_note,
            utcnow(),
        ),
    )


def get_pending_action(action_id: str) -> dict | None:
    rows = query("SELECT * FROM pending_actions WHERE action_id=?", (action_id,))
    if not rows:
        return None
    r = rows[0]
    r["segments"] = _loads(r.get("segments"), [])
    r["policies"] = _loads(r.get("policies"), [])
    return r


def resolve_pending_action(action_id: str, status: str) -> bool:
    cur = execute(
        "UPDATE pending_actions SET status=?, resolved_at=? WHERE action_id=? AND status='pending'",
        (status, utcnow(), action_id),
    )
    return cur.rowcount == 1


# --- audit ------------------------------------------------------------------


def add_audit(
    *,
    event: str,
    actor: str = "agent",
    session_id: str | None = None,
    detail: str = "",
    job_ids: list[str] | None = None,
) -> None:
    execute(
        "INSERT INTO audit(session_id, actor, event, detail, job_ids, created_at) VALUES (?,?,?,?,?,?)",
        (session_id, actor, event, detail, json.dumps(job_ids or []), utcnow()),
    )


def recent_audit(limit: int = 25) -> list[dict]:
    rows = query("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))
    for r in rows:
        r["job_ids"] = _loads(r.get("job_ids"), [])
    return rows


def reset_session_state() -> None:
    with _LOCK:
        conn = get_conn()
        for table in ("pending_actions", "decisions", "audit", "messages", "job_state"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()


# --- job state overlay ------------------------------------------------------


def load_job_states() -> dict[str, dict]:
    rows = query("SELECT * FROM job_state")
    return {r["job_id"]: r for r in rows}


def save_job_state(job_id: str, status: str, attempts: int, assignee: str | None = None) -> None:
    execute(
        "INSERT INTO job_state(job_id, status, attempts, assignee, updated_at) VALUES (?,?,?,?,?) "
        "ON CONFLICT(job_id) DO UPDATE SET status=excluded.status, attempts=excluded.attempts, "
        "assignee=excluded.assignee, updated_at=excluded.updated_at",
        (job_id, status, attempts, assignee, utcnow()),
    )

