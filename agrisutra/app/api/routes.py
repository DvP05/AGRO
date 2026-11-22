"""FastAPI routes — internal API contracts from architecture.md §13 (challenge mode)."""

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.catalogue.ingest import CatalogueStore
from app.dialogue.manager import DialogueManager
from app.storage import database as db

router = APIRouter(prefix="/api/v1")
_store: CatalogueStore | None = None
_manager: DialogueManager | None = None


def init(store: CatalogueStore, manager: DialogueManager) -> None:
    global _store, _manager
    _store, _manager = store, manager


class MessageRequest(BaseModel):
    text: str
    client_message_id: str | None = None     # idempotency key: a re-delivered message is processed once


class ConfirmRequest(BaseModel):
    preview_id: str


class AgentRequest(BaseModel):
    agent: str = "demo-agent"
    text: str | None = None


def _session(sid: str) -> dict:
    s = db.get_session(sid)
    if not s:
        raise HTTPException(404, "Session not found")
    return s


def _mgr() -> DialogueManager:
    if not _manager:
        raise HTTPException(500, "System not initialised")
    return _manager


# ── Sessions & messages ──────────────────────────────────────

@router.post("/sessions")
def create_session():
    sid = f"conv_{uuid.uuid4().hex[:12]}"
    out = db.create_session(sid)
    return {**out, "channel": "browser_simulator", "mode": "CHALLENGE_MOCK"}


@router.post("/sessions/{sid}/messages")
def send_message(sid: str, req: MessageRequest):
    _session(sid)
    if not req.text.strip():
        raise HTTPException(422, "Message text is empty")
    return _mgr().process_message(sid, req.text.strip(), req.client_message_id)


@router.get("/sessions/{sid}/messages")
def list_messages(sid: str):
    _session(sid)
    return {"messages": db.get_messages(sid)}


@router.get("/sessions/{sid}/summary")
def summary(sid: str):
    _session(sid)
    return _mgr().get_session_summary(sid)


@router.get("/sessions/{sid}/need-fingerprint")
def need_fingerprint(sid: str):
    _session(sid)
    return _mgr().state(sid)["fingerprint"].to_dict()


@router.get("/sessions/{sid}/evidence")
def evidence(sid: str):
    _session(sid)
    st = _mgr().state(sid)
    return {"latest": st["last_receipt"], "receipt_ids": st["receipts"],
            "superseded": st.get("superseded_receipts", [])}


@router.get("/sessions/{sid}/replay")
def replay(sid: str):
    """Decision Replay: every stored turn trace, built from the versions in force at the time."""
    _session(sid)
    return {"conversation_id": sid, "traces": db.get_traces(sid), "events": db.get_events(sid)}


# ── Orders ───────────────────────────────────────────────────

@router.post("/sessions/{sid}/order-confirmation")
def confirm_order(sid: str, req: ConfirmRequest):
    _session(sid)
    mid = f"msg_{uuid.uuid4().hex[:10]}"
    res = _mgr().confirm_preview(sid, req.preview_id, mid)
    if res.get("reply"):
        db.save_message(f"msg_{uuid.uuid4().hex[:10]}", sid, "agent", res["reply"])
    st = _mgr().state(sid)
    return {**{k: v for k, v in res.items() if k != "side_effects"}, "order": st["order"],
            "order_preview": st["order_preview"], "state": st["lead_status"]}


@router.get("/sessions/{sid}/mock-order")
def mock_order(sid: str):
    _session(sid)
    order = db.get_mock_order(sid)
    if not order:
        raise HTTPException(404, "No order for this session")
    return order


# ── Human handoff ────────────────────────────────────────────

@router.post("/sessions/{sid}/handoff")
def take_over(sid: str, req: AgentRequest):
    """Agent takes ownership: bot pauses until resume-bot."""
    _session(sid)
    h = _mgr().take_over(sid, req.agent)
    return {"handoff": h, "bot_paused": True}


@router.post("/sessions/{sid}/agent-message")
def agent_message(sid: str, req: AgentRequest):
    _session(sid)
    if not req.text:
        raise HTTPException(422, "text required")
    try:
        out = _mgr().agent_message(sid, req.agent, req.text)
    except PermissionError as e:
        raise HTTPException(409, str(e))
    from app.channels.whatsapp import service as wa          # WhatsApp chats: deliver to the customer's phone
    sent = wa.send_agent_message(sid, req.text)
    return {**out, "channel": "whatsapp" if sent is not None else "browser", "delivery": sent}


@router.post("/sessions/{sid}/resume-bot")
def resume_bot(sid: str, req: AgentRequest):
    _session(sid)
    h = _mgr().resume_bot(sid, req.agent)
    return {"handoff": h, "bot_paused": False}


@router.get("/handoffs")
def handoffs():
    return {"handoffs": db.list_handoffs()}


# ── Catalogue ────────────────────────────────────────────────

@router.get("/products")
def products(family: str | None = None):
    if not _store:
        raise HTTPException(500, "Catalogue not loaded")
    items = _store.get_family(family) if family else _store.get_all_products()
    return {"catalogue_version": _store.version, "products": items}


@router.get("/products/{product_id}/evidence")
def product_evidence(product_id: str):
    ev = _mgr().evidence_gate.build_product_evidence(product_id)
    if ev.get("status") == "NOT_FOUND":
        raise HTTPException(404, "Product not found")
    return ev


@router.get("/catalogue/report")
def catalogue_report():
    if not _store:
        raise HTTPException(500, "Catalogue not loaded")
    return _store.validation_report()
