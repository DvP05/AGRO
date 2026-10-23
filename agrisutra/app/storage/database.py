"""SQLite storage layer for AgriSutra sessions, events, and mock orders."""

import sqlite3
import json
import os
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
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE TABLE IF NOT EXISTS mock_orders (
    order_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL UNIQUE,
    product_id TEXT NOT NULL,
    product_name TEXT NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 1,
    unit TEXT DEFAULT 'piece',
    quoted_price REAL,
    currency TEXT DEFAULT 'INR',
    catalogue_version TEXT,
    confirmation_message_id TEXT NOT NULL,
    confirmed_at TEXT NOT NULL,
    order_type TEXT NOT NULL DEFAULT 'MOCK',
    payment_status TEXT NOT NULL DEFAULT 'PENDING',
    details TEXT DEFAULT '{}',
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_data TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);
"""


def get_db() -> sqlite3.Connection:
    """Get a database connection, creating the schema if needed."""
    os.makedirs(os.path.dirname(DB_PATH) if os.path.dirname(DB_PATH) else ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA_SQL)
    return conn


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Session CRUD ──────────────────────────────────────────────

def create_session(session_id: str) -> dict:
    conn = get_db()
    ts = now_iso()
    conn.execute(
        "INSERT INTO sessions (session_id, created_at, updated_at, lead_status) VALUES (?,?,?,?)",
        (session_id, ts, ts, "NEW"),
    )
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
    d["need_fingerprint"] = json.loads(d["need_fingerprint"])
    return d


def update_session(session_id: str, **fields) -> None:
    conn = get_db()
    sets = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values())
    vals.append(now_iso())
    vals.append(session_id)
    conn.execute(f"UPDATE sessions SET {sets}, updated_at = ? WHERE session_id = ?", vals)
    conn.commit()
    conn.close()


# ── Messages ──────────────────────────────────────────────────

def save_message(message_id: str, session_id: str, role: str, content: str,
                 intent: str = None, confidence: float = None) -> dict:
    conn = get_db()
    ts = now_iso()
    conn.execute(
        "INSERT INTO messages (message_id, session_id, role, content, intent, confidence, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (message_id, session_id, role, content, intent, confidence, ts),
    )
    conn.commit()
    conn.close()
    return {"message_id": message_id, "created_at": ts}


def get_messages(session_id: str) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at ASC",
        (session_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Mock Orders ───────────────────────────────────────────────

def create_mock_order(order_id: str, session_id: str, product_id: str,
                      product_name: str, quantity: int, unit: str,
                      quoted_price: float | None, currency: str,
                      catalogue_version: str, confirmation_message_id: str,
                      details: dict | None = None) -> dict:
    conn = get_db()
    ts = now_iso()
    conn.execute(
        "INSERT INTO mock_orders (order_id, session_id, product_id, product_name, "
        "quantity, unit, quoted_price, currency, catalogue_version, "
        "confirmation_message_id, confirmed_at, order_type, payment_status, details) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (order_id, session_id, product_id, product_name, quantity, unit,
         quoted_price, currency, catalogue_version, confirmation_message_id,
         ts, "MOCK", "PENDING", json.dumps(details or {})),
    )
    conn.commit()
    conn.close()
    return {"order_id": order_id, "confirmed_at": ts, "payment_status": "PENDING"}


def get_mock_order(session_id: str) -> dict | None:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM mock_orders WHERE session_id = ?", (session_id,)
    ).fetchone()
    conn.close()
    if row is None:
        return None
    d = dict(row)
    d["details"] = json.loads(d["details"])
    return d


# ── Audit Log ─────────────────────────────────────────────────

def log_event(session_id: str, event_type: str, event_data: dict) -> None:
    conn = get_db()
    conn.execute(
        "INSERT INTO audit_log (session_id, event_type, event_data, created_at) VALUES (?,?,?,?)",
        (session_id, event_type, json.dumps(event_data), now_iso()),
    )
    conn.commit()
    conn.close()
