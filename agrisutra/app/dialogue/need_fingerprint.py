"""Need Fingerprint — structured, editable customer requirement profile with provenance."""

import copy
from datetime import datetime, timezone
from typing import Any


class FieldStatus:
    KNOWN = "known"
    UNKNOWN = "unknown"
    SUPERSEDED = "superseded"
    CONFLICTING = "conflicting"


class NeedFingerprint:
    """Tracks customer requirements, preferences, unknowns, and provenance."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.requirements: dict[str, dict] = {
            "budget": {"value": None, "unit": "INR", "status": FieldStatus.UNKNOWN, "source": None},
            "intended_use": {"value": None, "status": FieldStatus.UNKNOWN, "source": None},
            "farm_size_acres": {"value": None, "status": FieldStatus.UNKNOWN, "source": None},
            "preferred_type": {"value": None, "status": FieldStatus.UNKNOWN, "source": None},
            "quantity": {"value": None, "status": FieldStatus.UNKNOWN, "source": None},
        }
        self.preferences: dict[str, Any] = {}
        self.contradictions: list[dict] = []
        self.history: list[dict] = []  # audit trail of all changes
        self.last_updated_by: str | None = None

    def update_field(self, field: str, value: Any, source: str, is_correction: bool = False) -> dict:
        """Update a requirement field with provenance tracking.
        
        Returns a change record describing what happened.
        """
        change = {
            "field": field,
            "new_value": value,
            "source": source,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": "correction" if is_correction else "update",
        }

        if field not in self.requirements:
            self.requirements[field] = {
                "value": None, "status": FieldStatus.UNKNOWN, "source": None
            }

        old_val = self.requirements[field]["value"]
        change["old_value"] = old_val

        if is_correction and old_val is not None and old_val != value:
            # Mark as superseded, record contradiction
            change["type"] = "correction"
            self.contradictions.append({
                "field": field,
                "old_value": old_val,
                "new_value": value,
                "old_source": self.requirements[field]["source"],
                "new_source": source,
            })

        self.requirements[field]["value"] = value
        self.requirements[field]["status"] = FieldStatus.KNOWN
        self.requirements[field]["source"] = source
        self.last_updated_by = source
        self.history.append(change)

        return change

    def update_from_entities(self, entities: dict, message_id: str) -> list[dict]:
        """Bulk update from extracted entities. Returns list of changes."""
        changes = []
        is_correction = entities.get("is_correction", False)

        field_map = {
            "budget": "budget",
            "intended_use": "intended_use",
            "farm_size_acres": "farm_size_acres",
            "preferred_type": "preferred_type",
            "quantity": "quantity",
        }

        for entity_key, field_name in field_map.items():
            if entity_key in entities:
                change = self.update_field(
                    field_name, entities[entity_key], message_id, is_correction
                )
                changes.append(change)

        return changes

    def get_known_fields(self) -> dict[str, Any]:
        """Return only fields with known values."""
        return {
            k: v["value"]
            for k, v in self.requirements.items()
            if v["status"] == FieldStatus.KNOWN and v["value"] is not None
        }

    def get_unknown_fields(self) -> list[str]:
        """Return fields that are still unknown."""
        return [
            k for k, v in self.requirements.items()
            if v["status"] == FieldStatus.UNKNOWN or v["value"] is None
        ]

    def get_completion_ratio(self) -> float:
        """How complete is the fingerprint? 0.0 to 1.0"""
        total = len(self.requirements)
        known = sum(
            1 for v in self.requirements.values()
            if v["status"] == FieldStatus.KNOWN and v["value"] is not None
        )
        return round(known / total, 2) if total > 0 else 0.0

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "requirements": copy.deepcopy(self.requirements),
            "preferences": copy.deepcopy(self.preferences),
            "contradictions": copy.deepcopy(self.contradictions),
            "last_updated_by": self.last_updated_by,
            "completion_ratio": self.get_completion_ratio(),
            "known_fields": self.get_known_fields(),
            "unknown_fields": self.get_unknown_fields(),
        }
