"""Need Fingerprint — structured, revisable customer requirement profile (M1).

Every update appends a revision record. A correction marks the earlier value
SUPERSEDED; nothing is deleted.
"""

import copy
from datetime import datetime, timezone
from typing import Any


class SlotStatus:
    KNOWN = "KNOWN"
    UNKNOWN = "UNKNOWN"
    SUPERSEDED = "SUPERSEDED"
    CONFLICTED = "CONFLICTED"


SLOT_SCHEMA: dict[str, dict] = {
    "product_family": {"kind": "REQUIREMENT", "label": "Product family"},
    "need_type": {"kind": "REQUIREMENT", "label": "Attachment or complete machine"},
    "trolley_configuration": {"kind": "HARD_CONSTRAINT", "label": "Brush cutter configuration"},
    "machine_model": {"kind": "INFO", "label": "Customer's machine make/model"},
    "exact_fitment_required": {"kind": "HARD_CONSTRAINT_IF_CUSTOMER_REQUIRES_IT", "label": "Exact fitment must be confirmed"},
    "dumper_required": {"kind": "HARD_CONSTRAINT", "label": "Dumper / tipping needed"},
    "capacity_required_litres": {"kind": "HARD_CONSTRAINT", "label": "Required capacity (L)"},
    "intended_use": {"kind": "PREFERENCE", "label": "Materials to move"},
    "needs_push_trolley": {"kind": "HARD_CONSTRAINT", "label": "Push-type trolley"},
    "warranty_min_months": {"kind": "HARD_CONSTRAINT", "label": "Minimum engine warranty (months)"},
    "warranty_preference": {"kind": "PREFERENCE", "label": "Warranty preference"},
    "run_time_preference": {"kind": "PREFERENCE", "label": "Running-time preference"},
    "carburetor_preference": {"kind": "PREFERENCE", "label": "Carburetor preference"},
    "budget": {"kind": "HARD_CONSTRAINT", "label": "Budget (₹)"},
    "quantity": {"kind": "ORDER_DETAIL", "label": "Quantity"},
}

# Slots that only make sense inside one product family.
FAMILY_SLOTS = {
    "brush_cutter_trolley_attachment": ["trolley_configuration", "machine_model", "exact_fitment_required"],
    "wheel_barrow": ["dumper_required", "capacity_required_litres", "intended_use"],
    "brush_cutter_machine": ["needs_push_trolley", "warranty_min_months", "warranty_preference", "carburetor_preference", "run_time_preference"],
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_slot(key: str) -> dict:
    return {"value": None, "kind": SLOT_SCHEMA[key]["kind"], "label": SLOT_SCHEMA[key]["label"],
            "status": SlotStatus.UNKNOWN, "confidence": 0.0, "source_message_id": None,
            "source_text": None, "captured_at": None, "supersedes": None}


class NeedFingerprint:
    def __init__(self, conversation_id: str):
        self.conversation_id = conversation_id
        self.revision = 0
        self.slots: dict[str, dict] = {k: _empty_slot(k) for k in SLOT_SCHEMA}
        self.history: list[dict] = []          # append-only revision log
        self.contradictions: list[dict] = []
        self.last_updated_message_id: str | None = None

    # ── Updates ──────────────────────────────────────────────

    def update_slot(self, key: str, value: Any, message_id: str, source_text: str = "",
                    confidence: float = 0.9, is_correction: bool = False) -> dict | None:
        if key not in self.slots or value is None:
            return None
        slot = self.slots[key]
        old = slot["value"]
        if old == value and slot["status"] == SlotStatus.KNOWN:
            return None

        self.revision += 1
        change_type = "set"
        if old is not None and old != value:
            change_type = "correction"
            prior = copy.deepcopy(slot)
            prior["status"] = SlotStatus.SUPERSEDED
            self.contradictions.append({
                "field": key, "label": slot["label"], "old_value": old, "new_value": value,
                "old_source": slot["source_message_id"], "new_source": message_id,
                "explicit_correction": is_correction, "resolved_by": "explicit correction" if is_correction else "recency",
                "revision": self.revision, "at": _now(),
            })
            slot_supersedes = prior
        else:
            slot_supersedes = None

        slot.update({"value": value, "status": SlotStatus.KNOWN, "confidence": round(confidence, 3),
                     "source_message_id": message_id, "source_text": source_text[:160], "captured_at": _now(),
                     "supersedes": {"value": slot_supersedes["value"], "source_message_id": slot_supersedes["source_message_id"],
                                    "status": SlotStatus.SUPERSEDED} if slot_supersedes else None})
        record = {"revision": self.revision, "field": key, "label": slot["label"], "type": change_type,
                  "old_value": old, "new_value": value, "source_message_id": message_id,
                  "confidence": round(confidence, 3), "at": slot["captured_at"]}
        self.history.append(record)
        self.last_updated_message_id = message_id
        return record

    def clear_slot(self, key: str, message_id: str, reason: str) -> dict | None:
        """Mark a slot as no longer applicable (e.g. family changed). Keeps history."""
        slot = self.slots.get(key)
        if not slot or slot["value"] is None:
            return None
        self.revision += 1
        record = {"revision": self.revision, "field": key, "label": slot["label"], "type": "cleared",
                  "old_value": slot["value"], "new_value": None, "source_message_id": message_id,
                  "reason": reason, "at": _now()}
        self.slots[key] = _empty_slot(key)
        self.slots[key]["supersedes"] = {"value": record["old_value"], "status": SlotStatus.SUPERSEDED}
        self.history.append(record)
        return record

    # ── Reads ────────────────────────────────────────────────

    def get(self, key: str) -> Any:
        return self.slots[key]["value"] if key in self.slots else None

    def known(self) -> dict[str, Any]:
        return {k: s["value"] for k, s in self.slots.items() if s["status"] == SlotStatus.KNOWN}

    def relevant_slots(self) -> list[str]:
        fam = self.get("product_family")
        base = ["product_family"]
        if fam == "brush_cutter_trolley_attachment" or fam is None:
            base.append("need_type")
        if fam:
            base += FAMILY_SLOTS.get(fam, [])
        return base + ["budget", "quantity"]

    def unknown(self) -> list[str]:
        return [k for k in self.relevant_slots() if self.slots[k]["status"] != SlotStatus.KNOWN]

    def completion_ratio(self) -> float:
        optional = {"INFO", "HARD_CONSTRAINT_IF_CUSTOMER_REQUIRES_IT", "ORDER_DETAIL", "PREFERENCE"}
        rel = [k for k in self.relevant_slots() if self.slots[k]["kind"] not in optional]
        return round(sum(1 for k in rel if self.slots[k]["status"] == SlotStatus.KNOWN) / len(rel), 2) if rel else 0.0

    def to_dict(self) -> dict:
        rel = set(self.relevant_slots())
        return {
            "conversation_id": self.conversation_id,
            "revision": self.revision,
            "slots": {k: {**copy.deepcopy(s), "relevant": k in rel} for k, s in self.slots.items()},
            "known_slots": [k for k in self.relevant_slots() if self.slots[k]["status"] == SlotStatus.KNOWN],
            "unknown_slots": self.unknown(),
            "contradictions": copy.deepcopy(self.contradictions),
            "history": copy.deepcopy(self.history[-30:]),
            "completion_ratio": self.completion_ratio(),
            "last_updated_message_id": self.last_updated_message_id,
        }
