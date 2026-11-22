"""AskSmart — Question Value Engine (M2).

QuestionValue(q) = Criticality(q) × InformationGain(q) × Coverage(q)
                   − λ × CustomerEffort(q) − μ × PrivacyCost(q)

Information gain is computed from how each question's possible answers
partition the *current* viable candidates (uniform prior over candidates; no
learned distribution is claimed).
"""

import math
from typing import Callable

from app.dialogue.need_fingerprint import NeedFingerprint

LAMBDA_EFFORT = 0.3
MU_PRIVACY = 0.5


def _cfg(p):
    f = p["facts"].get("configuration")
    return f["value"] if f and p["family"] == "brush_cutter_trolley_attachment" else None


def _tip(p):
    f = p["facts"].get("tipping_mechanism")
    return (not f.get("negative")) if f else None


def _push(p):
    if p["family"] != "brush_cutter_machine":
        return None
    f = p["facts"].get("configuration")
    return bool(f and "push" in f["value"])


def _warranty(p):
    f = p["facts"].get("engine_warranty")
    return f.get("months") if f else None


def _carb(p):
    f = p["facts"].get("carburetor")
    return ("walbro" if "walbro" in f["value"].lower() else "stable") if f else None


def _need(p):
    return {"brush_cutter_trolley_attachment": "attachment", "brush_cutter_machine": "complete_machine"}.get(p["family"])


QUESTIONS: list[dict] = [
    {"slot": "product_family", "criticality": 1.0, "effort": 0.1, "privacy": 0.0, "blocking": True,
     "text": "Which product are you looking for — a Wheel Barrow, a Brush Cutter Trolley (attachment), or a Brush Cutter machine?",
     "quick_replies": ["Wheel Barrow", "Brush Cutter Trolley", "Brush Cutter machine"], "answer": lambda p: p["family"]},
    {"slot": "need_type", "criticality": 1.0, "effort": 0.1, "privacy": 0.0, "blocking": True,
     "text": "Do you already own a brush cutter and need a trolley attachment for it, or do you want a complete brush cutter supplied with a push-type trolley?",
     "quick_replies": ["I already have a brush cutter", "I want a complete machine"], "answer": _need},
    {"slot": "trolley_configuration", "criticality": 1.0, "effort": 0.1, "privacy": 0.0, "blocking": False,
     "text": "Is your brush cutter a sidepack or a backpack model?",
     "quick_replies": ["Sidepack", "Backpack"], "answer": _cfg},
    {"slot": "dumper_required", "criticality": 0.9, "effort": 0.1, "privacy": 0.0, "blocking": False,
     "text": "Do you need easy tipping (a dumper mechanism), or is a standard wheel barrow fine?",
     "quick_replies": ["I need easy tipping", "Standard is fine"], "answer": _tip},
    {"slot": "needs_push_trolley", "criticality": 0.8, "effort": 0.1, "privacy": 0.0, "blocking": False,
     "text": "Do you want the machine on a push-type trolley, or a regular brush cutter?",
     "quick_replies": ["Push-type trolley", "Regular brush cutter"], "answer": _push},
    {"slot": "warranty_min_months", "criticality": 0.7, "effort": 0.2, "privacy": 0.0, "blocking": False,
     "text": "How important is engine warranty to you? The catalogue lists none, 6 months, or 1 year depending on the model.",
     "quick_replies": ["1 year warranty needed", "6 months is fine", "Warranty doesn't matter"], "answer": _warranty},
    {"slot": "carburetor_preference", "criticality": 0.5, "effort": 0.2, "privacy": 0.0, "blocking": False,
     "text": "Do you have a carburetor preference — WALBRO or the stable carburetor?",
     "quick_replies": ["WALBRO", "Stable carburetor", "No preference"], "answer": _carb},
    {"slot": "budget", "criticality": 0.4, "effort": 0.3, "privacy": 0.1, "blocking": False,
     "text": "What budget do you have in mind?",
     "quick_replies": [], "answer": lambda p: p["price"].get("amount")},
]

FIXED_BASELINE_ORDER = ["product_family", "need_type", "trolley_configuration", "dumper_required",
                        "budget", "needs_push_trolley", "warranty_min_months", "carburetor_preference"]


def _entropy_uniform(n: int) -> float:
    return math.log2(n) if n > 1 else 0.0


def score_question(q: dict, candidates: list[dict]) -> dict:
    answer: Callable = q["answer"]
    applicable = [(p, answer(p)) for p in candidates]
    applicable = [(p, a) for p, a in applicable if a is not None]
    partitions: dict[str, list[str]] = {}
    for p, a in applicable:
        partitions.setdefault(str(a), []).append(p["product_id"])
    n = len(applicable)
    if n < 2 or len(partitions) < 2:
        ig = 0.0
    else:
        ig = _entropy_uniform(n) - sum(len(ids) / n * _entropy_uniform(len(ids)) for ids in partitions.values())
    coverage = n / len(candidates) if candidates else 0.0
    value = q["criticality"] * ig * coverage - LAMBDA_EFFORT * q["effort"] - MU_PRIVACY * q["privacy"]
    return {
        "slot": q["slot"], "question": q["text"], "quick_replies": q["quick_replies"],
        "information_gain": round(ig, 3), "criticality": q["criticality"], "coverage": round(coverage, 2),
        "effort_cost": round(LAMBDA_EFFORT * q["effort"], 3), "privacy_cost": round(MU_PRIVACY * q["privacy"], 3),
        "value": round(value if ig > 0 else 0.0, 3), "blocking": q["blocking"] and ig > 0,
        "partitions": partitions, "candidate_count": len(candidates),
    }


class QuestionValueEngine:
    def score_all(self, fp: NeedFingerprint, candidates: list[dict], skip: set[str] | None = None) -> list[dict]:
        skip = skip or set()
        known = fp.known()
        out = [score_question(q, candidates) for q in QUESTIONS
               if q["slot"] not in known and q["slot"] not in skip]
        out = [s for s in out if s["coverage"] > 0]       # questions that don't apply to any candidate are noise
        out.sort(key=lambda s: (not s["blocking"], -s["value"]))
        return out

    def select_next(self, fp: NeedFingerprint, candidates: list[dict], skip: set[str] | None = None) -> dict | None:
        scored = [s for s in self.score_all(fp, candidates, skip) if s["value"] > 0]
        return scored[0] if scored else None
