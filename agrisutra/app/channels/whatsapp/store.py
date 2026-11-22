"""Persistence for the WhatsApp channel: conversation mapping, webhook inbox, outbox, media cache.

Phone numbers (wa_id) are personal data: they live only in `wa_conversations` and the inbox payload,
never in audit events or the cockpit (which shows a masked form).
"""

import hashlib
import json
import uuid
from datetime import datetime, timezone

from app.storage import database as db

SCHEMA = """
CREATE TABLE IF NOT EXISTS wa_conversations (
    wa_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL UNIQUE,
    profile_name TEXT,
    referral TEXT,
    last_inbound_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS wa_inbox (
    event_key TEXT PRIMARY KEY,
    wa_id TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    received_at TEXT NOT NULL,
    processed_at TEXT
);
CREATE TABLE IF NOT EXISTS wa_outbox (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    wa_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    provider_message_id TEXT,
    delivery_status TEXT,
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS wa_media (
    sha256 TEXT PRIMARY KEY,
    media_id TEXT NOT NULL,
    uploaded_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conn():
    conn = db.get_db()
    conn.executescript(SCHEMA)
    return conn


def mask(wa_id: str) -> str:
    return ("•" * max(len(wa_id) - 4, 0)) + wa_id[-4:] if wa_id else ""


# ── Conversations ────────────────────────────────────────────

def get_or_create_conversation(wa_id: str, profile_name: str | None = None, referral: dict | None = None) -> dict:
    conn = _conn()
    row = conn.execute("SELECT * FROM wa_conversations WHERE wa_id = ?", (wa_id,)).fetchone()
    if row:
        if profile_name and profile_name != row["profile_name"]:
            conn.execute("UPDATE wa_conversations SET profile_name = ? WHERE wa_id = ?", (profile_name, wa_id))
            conn.commit()
        conn.close()
        return {**dict(row), "created": False}
    session_id = f"wa_{uuid.uuid4().hex[:12]}"
    conn.close()
    db.create_session(session_id)
    conn = _conn()
    conn.execute("INSERT INTO wa_conversations VALUES (?,?,?,?,?,?)",
                 (wa_id, session_id, profile_name, json.dumps(referral) if referral else None, None, _now()))
    conn.commit()
    conn.close()
    return {"wa_id": wa_id, "session_id": session_id, "profile_name": profile_name, "created": True}


def touch_inbound(wa_id: str, at: str) -> None:
    conn = _conn()
    conn.execute("UPDATE wa_conversations SET last_inbound_at = ? WHERE wa_id = ?", (at, wa_id))
    conn.commit()
    conn.close()


def conversation_for_session(session_id: str) -> dict | None:
    conn = _conn()
    row = conn.execute("SELECT * FROM wa_conversations WHERE session_id = ?", (session_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_conversations(limit: int = 50) -> list[dict]:
    conn = _conn()
    rows = conn.execute(
        "SELECT c.*, s.lead_status, s.updated_at FROM wa_conversations c JOIN sessions s ON s.session_id = c.session_id "
        "ORDER BY COALESCE(c.last_inbound_at, c.created_at) DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [{"session_id": r["session_id"], "customer": mask(r["wa_id"]), "profile_name": r["profile_name"],
             "lead_status": r["lead_status"], "last_inbound_at": r["last_inbound_at"]} for r in rows]


# ── Inbox (deduplicated webhook events) ──────────────────────

def inbox_insert(event_key: str, wa_id: str, payload: dict) -> bool:
    """True if newly stored; False for a duplicate delivery (Meta retries webhooks)."""
    raw = json.dumps(payload, sort_keys=True)
    conn = _conn()
    cur = conn.execute("INSERT OR IGNORE INTO wa_inbox (event_key, wa_id, payload_hash, payload_json, status, received_at) "
                       "VALUES (?,?,?,?,?,?)",
                       (event_key, wa_id, hashlib.sha256(raw.encode()).hexdigest(), raw, "RECEIVED", _now()))
    conn.commit()
    conn.close()
    return cur.rowcount == 1


def inbox_claim(event_key: str) -> dict | None:
    """Atomically move RECEIVED/FAILED → PROCESSING so one event is processed once."""
    conn = _conn()
    cur = conn.execute("UPDATE wa_inbox SET status = 'PROCESSING', attempts = attempts + 1 "
                       "WHERE event_key = ? AND status IN ('RECEIVED', 'FAILED') AND attempts < 3", (event_key,))
    conn.commit()
    row = conn.execute("SELECT * FROM wa_inbox WHERE event_key = ?", (event_key,)).fetchone() if cur.rowcount else None
    conn.close()
    return {**dict(row), "payload": json.loads(row["payload_json"])} if row else None


def inbox_finish(event_key: str, status: str, error: str | None = None) -> None:
    conn = _conn()
    conn.execute("UPDATE wa_inbox SET status = ?, error = ?, processed_at = ? WHERE event_key = ?",
                 (status, error, _now(), event_key))
    conn.commit()
    conn.close()


def inbox_next_for(wa_id: str) -> str | None:
    """Oldest unprocessed event for one customer, by WhatsApp's own timestamp — preserves message order."""
    conn = _conn()
    row = conn.execute(
        "SELECT event_key FROM wa_inbox WHERE wa_id = ? AND status IN ('RECEIVED','FAILED') AND attempts < 3 "
        "ORDER BY CAST(json_extract(payload_json, '$.message.timestamp') AS INTEGER), received_at, rowid LIMIT 1",
        (wa_id,)).fetchone()
    conn.close()
    return row["event_key"] if row else None


def inbox_wa_id(event_key: str) -> str | None:
    conn = _conn()
    row = conn.execute("SELECT wa_id FROM wa_inbox WHERE event_key = ?", (event_key,)).fetchone()
    conn.close()
    return row["wa_id"] if row else None


def inbox_pending() -> list[str]:
    conn = _conn()
    rows = conn.execute("SELECT event_key FROM wa_inbox WHERE status IN ('RECEIVED','FAILED') AND attempts < 3 "
                        "ORDER BY received_at").fetchall()
    conn.close()
    return [r["event_key"] for r in rows]


# ── Outbox ───────────────────────────────────────────────────

def outbox_add(session_id: str, wa_id: str, seq: int, kind: str, payload: dict, status: str = "QUEUED") -> str:
    oid = f"out_{uuid.uuid4().hex[:12]}"
    conn = _conn()
    conn.execute("INSERT INTO wa_outbox (id, session_id, wa_id, seq, kind, payload_json, status, created_at, updated_at) "
                 "VALUES (?,?,?,?,?,?,?,?,?)", (oid, session_id, wa_id, seq, kind, json.dumps(payload), status, _now(), _now()))
    conn.commit()
    conn.close()
    return oid


def outbox_update(oid: str, **fields) -> None:
    conn = _conn()
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE wa_outbox SET {sets}, updated_at = ? WHERE id = ?", [*fields.values(), _now(), oid])
    conn.commit()
    conn.close()


def outbox_status_by_provider_id(provider_id: str, delivery_status: str, error: str | None = None) -> None:
    conn = _conn()
    conn.execute("UPDATE wa_outbox SET delivery_status = ?, error = COALESCE(?, error), updated_at = ? "
                 "WHERE provider_message_id = ?", (delivery_status, error, _now(), provider_id))
    conn.commit()
    conn.close()


def outbox_for_session(session_id: str, limit: int = 60) -> list[dict]:
    conn = _conn()
    rows = conn.execute("SELECT * FROM wa_outbox WHERE session_id = ? ORDER BY created_at DESC, seq DESC LIMIT ?",
                        (session_id, limit)).fetchall()
    conn.close()
    out = []
    for r in reversed(rows):
        d = dict(r)
        d["payload"] = json.loads(d.pop("payload_json"))
        d["wa_id"] = mask(d["wa_id"])
        d["payload"]["to"] = mask(d["payload"].get("to", ""))
        out.append(d)
    return out


# ── Media cache (live image uploads) ─────────────────────────

def media_get(sha: str) -> str | None:
    conn = _conn()
    row = conn.execute("SELECT media_id FROM wa_media WHERE sha256 = ?", (sha,)).fetchone()
    conn.close()
    return row["media_id"] if row else None


def media_put(sha: str, media_id: str) -> None:
    conn = _conn()
    conn.execute("INSERT OR REPLACE INTO wa_media VALUES (?,?,?)", (sha, media_id, _now()))
    conn.commit()
    conn.close()
