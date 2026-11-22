"""Inbound processing and outbound dispatch for WhatsApp, with the policy gates from architecture.md §15."""

import json
import threading
from datetime import datetime, timedelta, timezone

from app.channels.whatsapp import formatter, store
from app.channels.whatsapp.config import get_config
from app.channels.whatsapp.transport import get_transport
from app.storage import database as db

SERVICE_WINDOW = timedelta(hours=24)
UNSUPPORTED_REPLY = ("Thanks! I can read text messages and button taps for now. Please type what you're looking "
                     "for — for example \"I need a wheel barrow\" — or ask for a salesperson.")

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_manager = None
_transport_override = None          # tests inject a fake transport here


def init(manager) -> None:
    global _manager
    _manager = manager


def set_transport(transport) -> None:
    global _transport_override
    _transport_override = transport


def _transport():
    return _transport_override or get_transport(get_config())


def _lock(wa_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(wa_id, threading.Lock())


def _iso(ts: str | None) -> str:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return datetime.now(timezone.utc).isoformat()


# ── Webhook intake (fast: persist and return) ────────────────

def ingest(body: dict) -> list[str]:
    """Store new message events; apply delivery statuses. Returns the customers (wa_ids) with new messages."""
    customers: list[str] = []
    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            names = {c.get("wa_id"): (c.get("profile") or {}).get("name") for c in value.get("contacts", [])}
            for msg in value.get("messages", []):
                wa_id = msg.get("from")
                if not wa_id or not msg.get("id"):
                    continue
                event = {"message": msg, "profile_name": names.get(wa_id)}
                if store.inbox_insert(msg["id"], wa_id, event) and wa_id not in customers:
                    customers.append(wa_id)
            for st in value.get("statuses", []):
                err = (st.get("errors") or [{}])[0].get("title") if st.get("errors") else None
                store.outbox_status_by_provider_id(st.get("id", ""), st.get("status", "unknown"), err)
    return customers


# ── Worker: inbound messages → dialogue turns → outbound replies ──

def process_customer(wa_id: str) -> int:
    """Drain one customer's pending messages strictly in WhatsApp-timestamp order, one at a time.

    Two webhooks for the same customer can arrive together; whichever background task gets the lock
    processes everything queued in order, so "I'll take 2" can never be handled before the message
    that preceded it.
    """
    n = 0
    with _lock(wa_id):
        while True:
            key = store.inbox_next_for(wa_id)
            if not key:
                return n
            ev = store.inbox_claim(key)
            if not ev:
                return n
            try:
                _handle(ev)
                n += 1
            except Exception:
                return n                                   # left as FAILED for retry; don't skip ahead of it


def process_event(event_key: str) -> dict | None:
    """Process one specific event (retries / tests). Holds the customer's lock."""
    ev = store.inbox_claim(event_key)
    if not ev:
        return None                                        # already processed, or retries exhausted
    with _lock(ev["wa_id"]):
        return _handle(ev)


def _handle(ev: dict) -> dict | None:
    event_key, msg, wa_id = ev["event_key"], ev["payload"]["message"], ev["wa_id"]
    try:
        conv = store.get_or_create_conversation(wa_id, ev["payload"].get("profile_name"), msg.get("referral"))
        sid = conv["session_id"]
        store.touch_inbound(wa_id, _iso(msg.get("timestamp")))
        parsed = formatter.parse_inbound(msg)
        if msg.get("type") in ("reaction", "unsupported", "system"):
            store.inbox_finish(event_key, "IGNORED")
            return None
        if not parsed["text"]:
            st = _manager.state(sid)
            db.save_message(f"msg_{event_key[-10:]}", sid, "user", f"[{parsed['kind']} message]")
            if not st["bot_paused"]:
                dispatch(sid, wa_id, [{"type": "text", "text": {"body": UNSUPPORTED_REPLY}}], source="bot")
            store.inbox_finish(event_key, "PROCESSED_UNSUPPORTED_TYPE")
            return None
        payload = _manager.process_message(sid, parsed["text"], client_message_id=event_key,
                                           bound_preview_id=parsed.get("bound_preview_id"), channel="whatsapp")
        if not payload.get("duplicate_delivery") and payload.get("reply"):
            dispatch(sid, wa_id, formatter.build_messages(payload), source="bot")
        store.inbox_finish(event_key, "PROCESSED")
        return payload
    except Exception as e:                                 # keep the event for retry; never lose a customer message
        store.inbox_finish(event_key, "FAILED", f"{type(e).__name__}: {e}")
        db.log_event("wa-inbox", "WHATSAPP_PROCESSING_FAILED", {"event_key": event_key, "error": str(e)[:300]})
        raise


def process_pending() -> int:
    """Re-run stored events that failed or were never processed (e.g. after a restart), per customer in order."""
    customers = []
    for key in store.inbox_pending():
        ev_wa = store.inbox_wa_id(key)
        if ev_wa and ev_wa not in customers:
            customers.append(ev_wa)
    return sum(process_customer(wa) for wa in customers)


# ── Outbound with policy gates ───────────────────────────────

def dispatch(session_id: str, wa_id: str, messages: list[dict], source: str) -> list[dict]:
    """Gate, record and send. `source` is 'bot' or 'agent'. Every attempt lands in the outbox."""
    conv = store.conversation_for_session(session_id) or {}
    results = []
    transport = _transport()
    for seq, msg in enumerate(messages):
        status, reason = None, None
        last_in = conv.get("last_inbound_at")
        if source == "bot" and _manager and _manager.state(session_id).get("bot_paused"):
            status, reason = "SUPPRESSED_HUMAN_OWNED", "a salesperson owns this conversation"
        elif not last_in or datetime.now(timezone.utc) - datetime.fromisoformat(last_in) > SERVICE_WINDOW:
            status, reason = "SUPPRESSED_OUTSIDE_WINDOW", "outside the 24-hour customer service window (needs an approved template)"
        oid = store.outbox_add(session_id, wa_id, seq, f"{source}:{msg.get('type')}", _public(msg),
                               status or "QUEUED")
        if status:
            store.outbox_update(oid, error=reason)
            results.append({"id": oid, "status": status, "error": reason})
            continue
        try:
            msg = transport.resolve_media(dict(msg))
            res = transport.send(wa_id, msg)
        except Exception as e:
            res = {"status": "FAILED", "provider_message_id": None, "error": f"{type(e).__name__}: {e}"}
        store.outbox_update(oid, status=res["status"], provider_message_id=res.get("provider_message_id"),
                            error=res.get("error"), attempts=1, payload_json=json.dumps(_public(msg)))
        results.append({"id": oid, **res})
    return results


def _public(msg: dict) -> dict:
    return {k: v for k, v in msg.items() if not k.startswith("_")}


def send_agent_message(session_id: str, text: str) -> list[dict] | None:
    """A salesperson's reply from the cockpit goes to the customer's WhatsApp, if this is a WhatsApp chat."""
    conv = store.conversation_for_session(session_id)
    if not conv:
        return None
    return dispatch(session_id, conv["wa_id"], [{"type": "text", "text": {"body": text, "preview_url": False}}],
                    source="agent")
