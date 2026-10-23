"""FastAPI routes for AgriSutra sessions, messages, and orders."""

import uuid
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.dialogue.manager import DialogueManager
from app.catalogue.ingest import CatalogueStore
from app.storage import database as db


router = APIRouter(prefix="/api")

# ── Shared state (initialised in main.py) ─────────────────────
_store: CatalogueStore | None = None
_manager: DialogueManager | None = None


def init(store: CatalogueStore, manager: DialogueManager):
    global _store, _manager
    _store = store
    _manager = manager


# ── Request/Response Models ───────────────────────────────────

class MessageRequest(BaseModel):
    text: str


class SessionResponse(BaseModel):
    session_id: str
    created_at: str | None = None
    lead_status: str = "NEW"


# ── Routes ────────────────────────────────────────────────────

@router.post("/session", response_model=SessionResponse)
def create_session():
    """Start a new isolated demo session."""
    session_id = f"sess-{uuid.uuid4().hex[:12]}"
    result = db.create_session(session_id)
    return SessionResponse(**result)


@router.post("/session/{session_id}/message")
def send_message(session_id: str, req: MessageRequest):
    """Process a customer message and return the AI reply plus full state."""
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    if not _manager:
        raise HTTPException(500, "System not initialised")

    result = _manager.process_message(session_id, req.text)
    return result


@router.get("/session/{session_id}/summary")
def get_summary(session_id: str):
    """Retrieve the lead summary and unresolved requirements."""
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    if not _manager:
        raise HTTPException(500, "System not initialised")

    return _manager.get_session_summary(session_id)


@router.get("/session/{session_id}/messages")
def get_messages(session_id: str):
    """Get all messages for a session."""
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    return {"messages": db.get_messages(session_id)}


@router.get("/session/{session_id}/mock-order")
def get_mock_order(session_id: str):
    """View the mock order for a session."""
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    order = db.get_mock_order(session_id)
    if not order:
        raise HTTPException(404, "No order for this session")
    return order


@router.get("/catalogue")
def get_catalogue():
    """Return the full catalogue (for debug/judge panel)."""
    if not _store:
        raise HTTPException(500, "Catalogue not loaded")
    return {
        "version": _store.version,
        "product_count": len(_store.products),
        "categories": _store.get_categories(),
        "products": _store.get_all_products(),
        "warnings": _store.warnings,
    }


@router.get("/catalogue/{product_id}/evidence")
def get_product_evidence(product_id: str):
    """Get the full evidence summary for a product."""
    if not _manager:
        raise HTTPException(500, "System not initialised")
    evidence = _manager.evidence_gate.build_product_evidence(product_id)
    if evidence.get("status") == "NOT_FOUND":
        raise HTTPException(404, "Product not found")
    return evidence
