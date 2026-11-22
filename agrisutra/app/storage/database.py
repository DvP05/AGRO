"""SQLite persistence for the challenge prototype (same logical schema as the production PostgreSQL design)."""

import json
import os
import sqlite3
from datetime import datetime, timezone

DB_PATH = os.environ.get("DATABASE_PATH", "./data/agrisutra.db")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    lead_status TEXT NOT NULL DEFAULT 'NEW',
    need_fingerprint TEXT DEFAULT '{}',
    summary TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS messages (
    message_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    intent TEXT,
    confidence REAL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS processed_messages (
    session_id TEXT NOT NULL,
    client_message_id TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (session_id, client_message_id)
);
CREATE TABLE IF NOT EXISTS mock_orders (
    order_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL UNIQUE,
    product_id TEXT NOT NULL,
    product_name TEXT NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 1,
    unit TEXT DEFAULT 'each',
    quoted_price REAL,
    currency TEXT DEFAULT 'INR',
    catalogue_version TEXT,
    confirmation_message_id TEXT NOT NULL,
    confirmed_at TEXT NOT NULL,
    order_type TEXT NOT NULL DEFAULT 'MOCK',
    payment_status TEXT NOT NULL DEFAULT 'NOT_APPLICABLE',
    details TEXT DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS order_confirmations (
    idempotency_key TEXT PRIMARY KEY,
    order_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decision_traces (
    trace_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    turn INTEGER NOT NULL,
    trace_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS handoffs (
    handoff_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    queue TEXT NOT NULL,
    status TEXT NOT NULL,
    assigned_agent TEXT,
    context_snapshot TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS follow_ups (
    follow_up_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    purpose TEXT NOT NULL,
    requested_text TEXT,
    consent_snapshot TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_data TEXT DEFAULT '{}',
    created_at TEXT NOT NULL
);
"""

_initialised: set[str] = set()


def get_db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    if DB_PATH not in _initialised:
        conn.execute("PRAGMA journal_mode=WAL")
        _migrate_legacy(conn)
        conn.executescript(SCHEMA_SQL)
        _initialised.add(DB_PATH)
    return conn


def _migrate_legacy(conn: sqlite3.Connection) -> None:
    """Older prototype DBs had a mock_orders table with different defaults; keep data, add nothing destructive."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(mock_orders)").fetchall()}
    if cols and "order_type" not in cols:
        conn.execute("ALTER TABLE mock_orders ADD COLUMN order_type TEXT NOT NULL DEFAULT 'MOCK'")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _loads(s: str | None, default):
    try:
        return json.loads(s) if s else default
    except Exception:
        return default


# ── Sessions ─────────────────────────────────────────────────

def create_session(session_id: str) -> dict:
    conn = get_db()
    ts = now_iso()
    conn.execute("INSERT OR IGNORE INTO sessions (session_id, created_at, updated_at, lead_status) VALUES (?,?,?,?)",
                 (session_id, ts, ts, "NEW"))
    conn.commit()
    conn.close()
    return {"session_id": session_id, "created_at": ts, "lead_status": "NEW"}


def get_session(session_id: str) -> dict | None:
    conn = get_db()
    row = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
    conn.close()
    if row is None:
        return None
    d = dict(row)
    d["need_fingerprint"] = _loads(d.get("need_fingerprint"), {})
    return d


def update_session(session_id: str, **fields) -> None:
    conn = get_db()
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE sessions SET {sets}, updated_at = ? WHERE session_id = ?",
                 [*fields.values(), now_iso(), session_id])
    conn.commit()
    conn.close()


# ── Messages / idempotency ───────────────────────────────────

def save_message(message_id: str, session_id: str, role: str, content: str,
                 intent: str | None = None, confidence: float | None = None) -> dict:
    conn = get_db()
    ts = now_iso()
    conn.execute("INSERT INTO messages (message_id, session_id, role, content, intent, confidence, created_at) "
                 "VALUES (?,?,?,?,?,?,?)", (message_id, session_id, role, content, intent, confidence, ts))
    conn.commit()
    conn.close()
    return {"message_id": message_id, "created_at": ts}


def get_messages(session_id: str) -> list[dict]:
    conn = get_db()
    rows = conn.execute("SELECT * FROM messages WHERE session_id = ? ORDER BY created_at ASC, rowid ASC",
                        (session_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_processed(session_id: str, client_message_id: str) -> dict | None:
    conn = get_db()
    row = conn.execute("SELECT response_json FROM processed_messages WHERE session_id = ? AND client_message_id = ?",
                       (session_id, client_message_id)).fetchone()
    conn.close()
    return _loads(row["response_json"], None) if row else None


def save_processed(session_id: str, client_message_id: str, response: dict) -> None:
    conn = get_db()
    conn.execute("INSERT OR IGNORE INTO processed_messages VALUES (?,?,?,?)",
                 (session_id, client_message_id, json.dumps(response, default=str), now_iso()))
    conn.commit()
    conn.close()


# ── Mock orders ──────────────────────────────────────────────

def create_mock_order(order_id: str, session_id: str, product_id: str, product_name: str, quantity: int,
                      unit: str, quoted_price: float | None, currency: str, catalogue_version: str,
                      confirmation_message_id: str, idempotency_key: str, details: dict | None = None) -> dict:
    """Insert once. Returns {'created': bool, 'order': row}. Unique keys make duplicates impossible."""
    conn = get_db()
    ts = now_iso()
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute("SELECT order_id FROM order_confirmations WHERE idempotency_key = ?",
                                (idempotency_key,)).fetchone()
        existing = existing or conn.execute("SELECT order_id FROM mock_orders WHERE session_id = ?",
                                            (session_id,)).fetchone()
        if existing:
            conn.execute("ROLLBACK")
            conn.close()
            return {"created": False, "order": get_mock_order(session_id)}
        conn.execute(
            "INSERT INTO mock_orders (order_id, session_id, product_id, product_name, quantity, unit, quoted_price, "
            "currency, catalogue_version, confirmation_message_id, confirmed_at, order_type, payment_status, details) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (order_id, session_id, product_id, product_name, quantity, unit, quoted_price, currency,
             catalogue_version, confirmation_message_id, ts, "MOCK", "NOT_APPLICABLE", json.dumps(details or {})))
        conn.execute("INSERT INTO order_confirmations VALUES (?,?,?)", (idempotency_key, order_id, ts))
        conn.execute("COMMIT")
    except sqlite3.IntegrityError:
        conn.execute("ROLLBACK")
        conn.close()
        return {"created": False, "order": get_mock_order(session_id)}
    conn.close()
    return {"created": True, "order": get_mock_order(session_id)}


def update_order_details(session_id: str, patch: dict) -> dict | None:
    """Merge `patch` into the order's details (e.g. the customer's name/area for the sales follow-up)."""
    order = get_mock_order(session_id)
    if not order:
        return None
    details = {**order["details"], **patch}
    conn = get_db()
    conn.execute("UPDATE mock_orders SET details = ? WHERE session_id = ?", (json.dumps(details), session_id))
    conn.commit()
    conn.close()
    order["details"] = details
    return order


def get_mock_order(session_id: str) -> dict | None:
    conn = get_db()
    row = conn.execute("SELECT * FROM mock_orders WHERE session_id = ?", (session_id,)).fetchone()
    conn.close()
    if row is None:
        return None
    d = dict(row)
    d["details"] = _loads(d.get("details"), {})
    return d


# ── Decision traces (Decision Replay) ────────────────────────

def save_trace(trace: dict) -> None:
    conn = get_db()
    conn.execute("INSERT INTO decision_traces VALUES (?,?,?,?,?,?)",
                 (trace["trace_id"], trace["conversation_id"], trace["message_id"], trace["turn"],
                  json.dumps(trace, default=str), now_iso()))
    conn.commit()
    conn.close()


def get_traces(session_id: str) -> list[dict]:
    conn = get_db()
    rows = conn.execute("SELECT trace_json FROM decision_traces WHERE session_id = ? ORDER BY turn ASC",
                        (session_id,)).fetchall()
    conn.close()
    return [_loads(r["trace_json"], {}) for r in rows]


# ── Handoffs ─────────────────────────────────────────────────

def save_handoff(h: dict) -> None:
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO handoffs VALUES (?,?,?,?,?,?,?,?,?)",
                 (h["handoff_id"], h["session_id"], h["reason"], h["queue"], h["status"], h.get("assigned_agent"),
                  json.dumps(h["context_snapshot"], default=str), h["created_at"], now_iso()))
    conn.commit()
    conn.close()


def list_handoffs(session_id: str | None = None) -> list[dict]:
    conn = get_db()
    if session_id:
        rows = conn.execute("SELECT * FROM handoffs WHERE session_id = ? ORDER BY created_at DESC", (session_id,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM handoffs ORDER BY created_at DESC LIMIT 50").fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["context_snapshot"] = _loads(d["context_snapshot"], {})
        out.append(d)
    return out


# ── Follow-ups ───────────────────────────────────────────────

def save_follow_up(f: dict) -> None:
    conn = get_db()
    conn.execute("INSERT INTO follow_ups VALUES (?,?,?,?,?,?,?)",
                 (f["follow_up_id"], f["session_id"], f["purpose"], f.get("requested_text"),
                  json.dumps(f["consent_snapshot"]), f["status"], now_iso()))
    conn.commit()
    conn.close()


def suppress_follow_ups(session_id: str) -> int:
    conn = get_db()
    cur = conn.execute("UPDATE follow_ups SET status = 'SUPPRESSED_OPT_OUT' WHERE session_id = ? AND status = 'SCHEDULED_SIMULATED'",
                       (session_id,))
    conn.commit()
    conn.close()
    return cur.rowcount


def list_follow_ups(session_id: str) -> list[dict]:
    conn = get_db()
    rows = conn.execute("SELECT * FROM follow_ups WHERE session_id = ?", (session_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Audit ────────────────────────────────────────────────────

def log_event(session_id: str, event_type: str, event_data: dict) -> None:
    conn = get_db()
    conn.execute("INSERT INTO audit_log (session_id, event_type, event_data, created_at) VALUES (?,?,?,?)",
                 (session_id, event_type, json.dumps(event_data, default=str), now_iso()))
    conn.commit()
    conn.close()


def get_events(session_id: str) -> list[dict]:
    conn = get_db()
    rows = conn.execute("SELECT * FROM audit_log WHERE session_id = ? ORDER BY id ASC", (session_id,)).fetchall()
    conn.close()
    return [{**dict(r), "event_data": _loads(r["event_data"], {})} for r in rows]
