"""Human Takeover (M6): context packet, ownership lock, bot pause / resume."""

import uuid
from datetime import datetime, timezone

from app.storage import database as db

QUEUED, HUMAN_OWNED, RETURNED, CLOSED = "QUEUED", "HUMAN_OWNED", "RETURNED_TO_BOT", "CLOSED"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_packet(session_id: str, state: dict, reason: str, urgency: str = "normal") -> dict:
    fp = state["fingerprint"].to_dict()
    rec = state.get("last_recommendation") or {}
    receipt = state.get("last_receipt") or {}
    open_questions = [s for s in fp["unknown_slots"]]
    unresolved_facts = [c for c in receipt.get("claims", []) if c.get("evidence_status") != "SUPPORTED"]
    return {
        "conversation_id": session_id,
        "reason": reason,
        "urgency": urgency,
        "lead_state": state.get("lead_status"),
        "summary": state.get("summary_text", ""),
        "transcript": [{"role": m["role"], "text": m["content"], "at": m["created_at"]} for m in db.get_messages(session_id)],
        "need_fingerprint": {k: v for k, v in fp["slots"].items() if v["status"] == "KNOWN" or v.get("supersedes")},
        "corrections": fp["contradictions"],
        "products_considered": [{"product_id": e["product_id"], "name": e["short_name"], "status": e["status"],
                                 "reasons": [c["reason"] for c in e["checks"] if c["result"] != "PASS" and c["reason"]]}
                                for e in rec.get("evaluations", [])
                                if not any(c["criterion"] == "product_family" and c["result"] == "FAIL" for c in e["checks"])],
        "latest_truth_receipt_id": receipt.get("receipt_id"),
        "catalogue_version": rec.get("catalogue_version") or receipt.get("catalogue_version"),
        "dealguard": state.get("last_dealguard"),
        "order_preview": state.get("order_preview"),
        "order": state.get("order"),
        "open_questions": open_questions,
        "unresolved_facts": [{"product": c["subject_name"], "field": c["label"], "status": c["evidence_status"],
                              "note": c.get("note")} for c in unresolved_facts],
        "created_at": _now(),
    }


def request_handoff(session_id: str, state: dict, reason: str, queue: str = "sales") -> dict:
    """Queue a handoff (bot keeps answering until an agent takes ownership). Reuses an open one."""
    open_h = state.get("handoff")
    if open_h and open_h["status"] in (QUEUED, HUMAN_OWNED):
        open_h["reasons"] = sorted(set(open_h.get("reasons", [open_h["reason"]]) + [reason]))
        open_h["context_snapshot"] = build_packet(session_id, state, reason)
        db.save_handoff(open_h)
        return open_h
    h = {"handoff_id": f"ho_{uuid.uuid4().hex[:10]}", "session_id": session_id, "reason": reason,
         "reasons": [reason], "queue": queue, "status": QUEUED, "assigned_agent": None,
         "context_snapshot": build_packet(session_id, state, reason), "created_at": _now()}
    db.save_handoff(h)
    db.log_event(session_id, "HANDOFF_QUEUED", {"handoff_id": h["handoff_id"], "reason": reason})
    state["handoff"] = h
    return h


def take_over(session_id: str, state: dict, agent: str) -> dict:
    h = state.get("handoff") or request_handoff(session_id, state, "AGENT_INITIATED")
    h["status"] = HUMAN_OWNED
    h["assigned_agent"] = agent
    h["context_snapshot"] = build_packet(session_id, state, h["reason"])
    state["bot_paused"] = True
    state["owner"] = {"type": "human", "agent": agent, "since": _now()}
    db.save_handoff(h)
    db.log_event(session_id, "HUMAN_TOOK_OWNERSHIP", {"handoff_id": h["handoff_id"], "agent": agent})
    return h


def resume_bot(session_id: str, state: dict, agent: str) -> dict | None:
    h = state.get("handoff")
    state["bot_paused"] = False
    state["owner"] = {"type": "bot", "agent": None, "since": _now()}
    if h:
        h["status"] = RETURNED
        db.save_handoff(h)
    db.log_event(session_id, "BOT_RESUMED", {"by": agent})
    return h
