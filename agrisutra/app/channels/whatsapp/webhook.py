"""HTTP endpoints for the WhatsApp channel.

GET  /webhooks/whatsapp   Meta's verification handshake (hub.mode / hub.verify_token / hub.challenge)
POST /webhooks/whatsapp   Events. The X-Hub-Signature-256 HMAC is checked against the RAW body before
                          parsing, events are stored (deduplicated), 200 is returned at once, and the
                          dialogue runs in a background task.
GET  /api/v1/whatsapp/... Cockpit views: config status, conversations (masked numbers), outbox.
"""

import hashlib
import hmac
import json

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import PlainTextResponse

from app.channels.whatsapp import service, store
from app.channels.whatsapp.config import get_config

router = APIRouter()


def valid_signature(raw: bytes, header: str | None, secret: str) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.split("=", 1)[1])


@router.get("/webhooks/whatsapp", response_class=PlainTextResponse)
def verify(request: Request):
    cfg = get_config()
    q = request.query_params
    if (cfg.enabled and cfg.verify_token and q.get("hub.mode") == "subscribe"
            and hmac.compare_digest(q.get("hub.verify_token", ""), cfg.verify_token)):
        return q.get("hub.challenge", "")
    raise HTTPException(403, "Verification failed")


@router.post("/webhooks/whatsapp")
async def receive(request: Request, background: BackgroundTasks):
    cfg = get_config()
    if not cfg.enabled:
        raise HTTPException(404, "WhatsApp channel disabled")
    raw = await request.body()
    if cfg.app_secret:
        if not valid_signature(raw, request.headers.get("X-Hub-Signature-256"), cfg.app_secret):
            raise HTTPException(401, "Invalid signature")
    elif not cfg.allow_unsigned:
        raise HTTPException(401, "WHATSAPP_APP_SECRET not configured; refusing unsigned webhook")
    try:
        body = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "Invalid JSON")
    if body.get("object") != "whatsapp_business_account":
        return {"status": "ignored"}
    customers = service.ingest(body)
    for wa_id in customers:
        background.add_task(service.process_customer, wa_id)
    return {"status": "received", "new_events": len(customers)}


# ── Cockpit endpoints ────────────────────────────────────────

@router.get("/api/v1/whatsapp/status")
def status():
    return get_config().public()


@router.get("/api/v1/whatsapp/conversations")
def conversations():
    return {"conversations": store.list_conversations()}


@router.get("/api/v1/whatsapp/conversations/{session_id}/outbox")
def outbox(session_id: str):
    if not store.conversation_for_session(session_id):
        raise HTTPException(404, "Not a WhatsApp conversation")
    return {"messages": store.outbox_for_session(session_id)}


@router.post("/api/v1/whatsapp/retry-pending")
def retry_pending():
    return {"processed": service.process_pending()}
