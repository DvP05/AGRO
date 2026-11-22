"""Train the local intent model and evaluate it on the frozen held-out set.

Usage:  python scripts/train_nlu.py
Writes models/intent_model.joblib and data/evaluation/intent_report.json.
"""

import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sklearn.metrics import classification_report, f1_score  # noqa: E402

from app.nlu import model as intent_model  # noqa: E402
from app.nlu.predict import classify_intent, extract_entities  # noqa: E402


def evaluate() -> dict:
    with open(intent_model.HELDOUT_PATH, "r", encoding="utf-8") as f:
        heldout = json.load(f)
    texts = [t for t, _ in heldout["examples"]]
    gold = [g for _, g in heldout["examples"]]

    pipe = intent_model.get_model()["pipeline"]
    model_only = list(pipe.predict([t.lower() for t in texts]))
    hybrid = [classify_intent(t, extract_entities(t))[0] for t in texts]

    def summary(pred):
        return {
            "macro_f1": round(f1_score(gold, pred, average="macro", zero_division=0), 3),
            "accuracy": round(sum(p == g for p, g in zip(pred, gold)) / len(gold), 3),
            "errors": [{"text": t, "gold": g, "pred": p} for t, g, p in zip(texts, gold, pred) if p != g],
        }

    risky = [{"text": t, "gold": g, "pred": p} for t, g, p in zip(texts, gold, hybrid)
             if p == "CONFIRM_ORDER" and g != "CONFIRM_ORDER"]
    return {
        "model_version": intent_model.get_model()["model_version"],
        "heldout_version": heldout["version"],
        "heldout_size": len(gold),
        "model_only": summary(model_only),
        "hybrid_with_rules": summary(hybrid),
        "false_order_confirmations": risky,
        "per_class": classification_report(gold, hybrid, output_dict=True, zero_division=0),
    }


if __name__ == "__main__":
    bundle = intent_model.train(save=True)
    intent_model._BUNDLE = bundle
    print(f"Trained {bundle['model_version']} on {bundle['n_examples']} examples, {len(bundle['labels'])} intents")
    report = evaluate()
    out = os.path.join(ROOT, "data", "evaluation", "intent_report.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    for name in ("model_only", "hybrid_with_rules"):
        r = report[name]
        print(f"{name:18s} macro-F1={r['macro_f1']}  accuracy={r['accuracy']}  errors={len(r['errors'])}")
    print(f"False order confirmations on held-out set: {len(report['false_order_confirmations'])}")
    for err in report["hybrid_with_rules"]["errors"]:
        print(f"  ✗ {err['text']!r}: gold={err['gold']} pred={err['pred']}")
    print(f"Report written to {out}")
