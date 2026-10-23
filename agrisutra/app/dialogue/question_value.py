"""Question Value Engine — selects the most informative next question."""

from typing import Any
from app.catalogue.ingest import CatalogueStore
from app.dialogue.need_fingerprint import NeedFingerprint


# ── Question Templates ────────────────────────────────────────
QUESTIONS = {
    "budget": {
        "text": "What is your approximate budget for the sprayer?",
        "priority": 5,
        "discriminating_power": 0.8,
    },
    "intended_use": {
        "text": "What will you primarily use the sprayer for? (e.g., pesticides, herbicides, fertiliser, garden use)",
        "priority": 4,
        "discriminating_power": 0.6,
    },
    "farm_size_acres": {
        "text": "How large is the area you need to spray? (in acres)",
        "priority": 5,
        "discriminating_power": 0.9,
    },
    "preferred_type": {
        "text": "Do you have a preference for the sprayer type? (Manual, Battery-operated, Engine-powered, or Solar)",
        "priority": 3,
        "discriminating_power": 0.7,
    },
    "quantity": {
        "text": "How many units do you need?",
        "priority": 1,
        "discriminating_power": 0.1,
    },
}


class QuestionValueEngine:
    """Picks the single most useful next question based on information gain."""

    def __init__(self, store: CatalogueStore):
        self.store = store

    def compute_question_value(self, field: str, candidates: list[dict]) -> float:
        """Estimate the information gain of asking about a given field.
        
        Higher value = asking this question would reduce the candidate set more.
        """
        q_info = QUESTIONS.get(field)
        if not q_info:
            return 0.0

        if len(candidates) <= 1:
            return 0.0

        # Count how many distinct values this field takes across candidates
        values = set()
        for p in candidates:
            val = self._get_relevant_value(p, field)
            if val is not None:
                values.add(str(val))

        if len(values) <= 1:
            # All candidates have the same value — question won't help
            return 0.0

        # Split ratio: how evenly would the answer partition the set?
        split_ratio = len(values) / len(candidates)

        # Combined score
        value = (
            q_info["discriminating_power"] * 0.4 +
            split_ratio * 0.4 +
            (q_info["priority"] / 5.0) * 0.2
        )
        return round(value, 3)

    def _get_relevant_value(self, product: dict, field: str) -> Any:
        """Map a need-fingerprint field to a product's catalogue value."""
        if field == "budget":
            return product.get("price", {}).get("amount")
        elif field == "preferred_type":
            return product.get("category")
        elif field == "farm_size_acres":
            # Estimate from applications text
            apps = " ".join(product.get("applications", []))
            if "25 acres" in apps:
                return 25
            elif "10 acres" in apps:
                return 10
            elif "8 acres" in apps:
                return 8
            elif "5 acres" in apps:
                return 5
            elif "garden" in apps.lower():
                return 0.5
            return None
        elif field == "intended_use":
            return product.get("applications", [None])[0] if product.get("applications") else None
        return None

    def select_next_question(self, fingerprint: NeedFingerprint,
                             candidates: list[dict]) -> dict | None:
        """Select the single best question to ask next.
        
        Returns {"field": ..., "question": ..., "value_score": ...} or None.
        """
        unknown_fields = fingerprint.get_unknown_fields()

        if not unknown_fields:
            return None  # All fields are known

        scored = []
        for field in unknown_fields:
            if field in QUESTIONS:
                value = self.compute_question_value(field, candidates)
                scored.append({
                    "field": field,
                    "question": QUESTIONS[field]["text"],
                    "value_score": value,
                })

        if not scored:
            return None

        scored.sort(key=lambda x: x["value_score"], reverse=True)
        return scored[0]

    def get_all_scored_questions(self, fingerprint: NeedFingerprint,
                                 candidates: list[dict]) -> list[dict]:
        """Return all possible questions with their scores (for debug panel)."""
        unknown_fields = fingerprint.get_unknown_fields()
        scored = []
        for field in unknown_fields:
            if field in QUESTIONS:
                value = self.compute_question_value(field, candidates)
                scored.append({
                    "field": field,
                    "question": QUESTIONS[field]["text"],
                    "value_score": value,
                })
        scored.sort(key=lambda x: x["value_score"], reverse=True)
        return scored
