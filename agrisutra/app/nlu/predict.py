"""Intent classification and entity extraction — locally trained, no external LLM."""

import re
from typing import Any


# ── Intent Labels ─────────────────────────────────────────────
INTENTS = [
    "DISCOVER_PRODUCT",
    "ASK_PRODUCT_FACT",
    "COMPARE_PRODUCTS",
    "STATE_REQUIREMENT",
    "CORRECT_REQUIREMENT",
    "RAISE_OBJECTION",
    "EXPRESS_BUYING_INTENT",
    "CONFIRM_ORDER",
    "CANCEL_OR_STOP",
    "ASK_FOR_HUMAN",
    "GREETING",
    "UNKNOWN",
]

# ── Keyword-based intent classifier (rule-based baseline) ────
# In production this would be replaced by a trained TF-IDF + SVM model.
# For the hackathon prototype this rule engine provides deterministic,
# auditable behaviour that can be evaluated and improved.

INTENT_PATTERNS: list[tuple[str, list[str]]] = [
    ("CONFIRM_ORDER", [
        r"\b(confirm(ed)?(\s*(the|my|this)?\s*order)?|yes\s*(please|confirm|go\s*ahead|i\s*confirm)?|proceed(\s*with\s*(the\s*)?order)?|place\s*(the\s*)?order|book\s*it|finalize)\b",
    ]),
    ("GREETING", [
        r"\b(hi|hello|hey|good\s*(morning|afternoon|evening)|namaste|namaskar)\b",
    ]),
    ("CANCEL_OR_STOP", [
        r"\b(cancel|stop|no\s*thanks|not\s*interested|never\s*mind|forget\s*it|don'?t\s*want)\b",
    ]),
    ("ASK_FOR_HUMAN", [
        r"\b(human|agent|person|representative|talk\s*to\s*(someone|a\s*person)|escalate|support|help\s*me)\b",
    ]),
    ("CORRECT_REQUIREMENT", [
        r"\b(actually|correction|i\s*meant|change\s*(that|my|to)|instead\s*of|wait\b|sorry\s*i\s*meant|not\s*\w+\s*but|update\s*my|lower\s*to|reduce\s*to)\b",
    ]),
    ("COMPARE_PRODUCTS", [
        r"\b(compare|vs|versus|difference\s*between|which\s*(one\s*is\s*better|is\s*better)|comparison)\b",
    ]),
    ("EXPRESS_BUYING_INTENT", [
        r"\b(i\s*(want|would\s*like)\s*to\s*(buy|order|purchase|take|get)|i('ll|\s*will)\s*(take|buy|get)|buy\s*(this|it)|order\s*(this|it)|add\s*to\s*cart)\b",
    ]),
    ("RAISE_OBJECTION", [
        r"\b(too\s*(expensive|heavy|costly|much)|not\s*worth|cheaper|discount|offer|overpriced|why\s*so\s*(expensive|costly))\b",
    ]),
    ("ASK_PRODUCT_FACT", [
        r"\b(what\s*(is|are|about)|how\s*(much|long|many|heavy|far)|does\s*it|can\s*(i|it)|is\s*it|tell\s*me\s*about|any\s*warranty|what's\s*the\s*(price|weight|battery|capacity|pressure|warranty))\b",
        r"(warranty|price|weight|battery|capacity|pressure|nozzle|specifications?)\s*\?",
    ]),
    ("STATE_REQUIREMENT", [
        r"\b(i\s*need|i\s*want|looking\s*for|my\s*(budget|farm|requirement)|must\s*have|should\s*(have|be)|need\s*(a|something)|for\s*my\s*[\w\.]+\s*acre)\b",
    ]),
    ("DISCOVER_PRODUCT", [
        r"\b(show|sprayer|product|what\s*do\s*you\s*(have|sell|offer)|available|catalogue|catalog|range|options|recommend|suggest)\b",
    ]),
]


def classify_intent(text: str) -> tuple[str, float]:
    """Classify intent using pattern matching. Returns (intent, confidence)."""
    text_lower = text.lower().strip()

    if not text_lower or len(text_lower) < 2:
        return ("UNKNOWN", 0.0)

    scores: dict[str, float] = {}
    for intent, patterns in INTENT_PATTERNS:
        for pattern in patterns:
            matches = re.findall(pattern, text_lower)
            if matches:
                weight = 0.5 if intent in ("CONFIRM_ORDER", "GREETING", "CANCEL_OR_STOP", "ASK_FOR_HUMAN") else 0.4
                scores[intent] = scores.get(intent, 0) + len(matches) * weight

    if not scores:
        return ("UNKNOWN", 0.2)

    best = max(scores, key=scores.get)
    confidence = min(0.6 + scores[best] * 0.3, 0.95)

    # Disambiguate when multiple intents match
    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    if len(sorted_scores) > 1 and sorted_scores[0][1] - sorted_scores[1][1] < 0.1:
        # Priority resolution
        top_intents = [s[0] for s in sorted_scores[:2]]
        if "CONFIRM_ORDER" in top_intents:
            best = "CONFIRM_ORDER"
            confidence = 0.85
        elif "CORRECT_REQUIREMENT" in top_intents:
            best = "CORRECT_REQUIREMENT"
            confidence = 0.85

    return (best, round(confidence, 2))


# ── Entity Extraction ─────────────────────────────────────────

def extract_entities(text: str, product_names: list[str] = None) -> dict[str, Any]:
    """Extract structured entities from customer message."""
    text_lower = text.lower().strip()
    entities: dict[str, Any] = {}

    # Budget / price extraction
    price_patterns = [
        r'(?:budget|price|under|below|within|max|maximum|upto|up\s*to|around|about|rs\.?|₹|inr)(?:\s+is|\s*[:\-=])?\s*(\d[\d,]*)',
        r'(\d[\d,]*)\s*(?:rs|rupees|inr|₹)',
        r'₹\s*(\d[\d,]*)',
    ]
    for pattern in price_patterns:
        match = re.search(pattern, text_lower)
        if match:
            raw = match.group(1).replace(",", "")
            try:
                entities["budget"] = int(raw)
            except ValueError:
                pass
            break

    # Quantity extraction
    qty_patterns = [
        r'(\d+)\s*(?:pieces?|pcs?|units?|nos?|numbers?|quantity)',
        r'(?:quantity|qty|need|want|order)\s*[:\-]?\s*(\d+)',
    ]
    for pattern in qty_patterns:
        match = re.search(pattern, text_lower)
        if match:
            groups = match.groups()
            val = next(g for g in groups if g is not None)
            try:
                entities["quantity"] = int(val)
            except ValueError:
                pass
            break

    # Farm size / acreage
    acre_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:acre|acres|acr)', text_lower)
    if acre_match:
        entities["farm_size_acres"] = float(acre_match.group(1))

    # Sprayer type preference
    type_keywords = {
        "manual": "Manual Sprayer",
        "hand": "Manual Sprayer",
        "battery": "Battery Sprayer",
        "electric": "Battery Sprayer",
        "engine": "Engine Sprayer",
        "petrol": "Engine Sprayer",
        "motor": "Engine Sprayer",
        "solar": "Solar Sprayer",
    }
    for keyword, cat in type_keywords.items():
        if keyword in text_lower:
            entities["preferred_type"] = cat
            break

    # Use case / application
    use_keywords = {
        "pesticide": "Pesticide spraying",
        "herbicide": "Herbicide application",
        "fertiliser": "Fertiliser spraying",
        "fertilizer": "Fertiliser spraying",
        "garden": "Home use",
        "terrace": "Home use",
        "kitchen garden": "Home use",
        "orchard": "Orchard spraying",
        "plantation": "Plantation spraying",
        "farm": "Farm spraying",
    }
    for keyword, use in use_keywords.items():
        if keyword in text_lower:
            entities["intended_use"] = use
            break

    # Product name matching
    if product_names:
        for pname in product_names:
            if pname.lower() in text_lower or any(
                word in text_lower for word in pname.lower().split() if len(word) > 4
            ):
                entities["mentioned_product"] = pname
                break

    # Negation / correction cues
    correction_cues = ["actually", "instead", "change", "not that", "i meant", "wait",
                       "correction", "sorry", "no no", "lower", "higher", "reduce", "increase"]
    if any(cue in text_lower for cue in correction_cues):
        entities["is_correction"] = True

    return entities
