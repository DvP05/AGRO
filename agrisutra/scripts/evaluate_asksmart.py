"""Compare AskSmart with a fixed-questionnaire baseline on simulated customers.

For every catalogue product we simulate a customer who wants exactly that
product and answers each question truthfully from the catalogue. We count the
questions needed until exactly one viable candidate remains.

Usage:  python scripts/evaluate_asksmart.py   (writes data/evaluation/asksmart_report.json)
"""

import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.catalogue.ingest import CatalogueStore  # noqa: E402
from app.decision.fit import FitEngine  # noqa: E402
from app.dialogue.need_fingerprint import NeedFingerprint  # noqa: E402
from app.dialogue.question_value import FIXED_BASELINE_ORDER, QUESTIONS, QuestionValueEngine  # noqa: E402

Q = {q["slot"]: q for q in QUESTIONS}
MAX_TURNS = 8


def oracle_value(slot: str, target: dict):
    v = Q[slot]["answer"](target)
    if slot == "warranty_min_months" and v is not None:
        return v if v > 0 else None        # "warranty doesn't matter" for a no-warranty product
    return v


def viable(fit: FitEngine, fp: NeedFingerprint, store: CatalogueStore) -> list[dict]:
    rec = fit.recommend(fp)
    return [store.get_product(pid) for pid in rec["eligible"] + rec["unverified"]]


def simulate(policy: str, target: dict, store: CatalogueStore) -> dict:
    fit, engine = FitEngine(store), QuestionValueEngine()
    fp = NeedFingerprint("sim")
    asked, cands = [], store.get_all_products()
    declined: set[str] = set()
    while len(cands) > 1 and len(asked) < MAX_TURNS:
        if policy == "asksmart":
            q = engine.select_next(fp, cands, skip=declined)
            slot = q["slot"] if q else None
        else:
            slot = next((s for s in FIXED_BASELINE_ORDER if s not in fp.known() and s not in declined
                         and s not in asked), None)
        if not slot:
            break
        asked.append(slot)
        val = oracle_value(slot, target)
        if val is None:
            declined.add(slot)             # customer: "not applicable / doesn't matter"
        else:
            if slot == "budget":
                fp.update_slot("budget", target["price"]["amount"], "sim")
            else:
                fp.update_slot(slot, val, "sim")
        cands = viable(fit, fp, store)
    return {"questions": len(asked), "asked": asked, "resolved": [p["product_id"] for p in cands] == [target["product_id"]]}


def main():
    store = CatalogueStore(os.path.join(ROOT, "data", "catalogue", "sgb_catalogue.json"))
    rows = []
    for target in store.get_all_products():
        a, b = simulate("asksmart", target, store), simulate("fixed", target, store)
        rows.append({"target": target["product_id"], "asksmart": a, "fixed_baseline": b})
    summary = {
        policy: {
            "mean_questions": round(sum(r[policy]["questions"] for r in rows) / len(rows), 2),
            "resolved": sum(r[policy]["resolved"] for r in rows),
        } for policy in ("asksmart", "fixed_baseline")
    }
    report = {"method": "oracle customers, one per catalogue product; count questions until one viable candidate remains",
              "n_targets": len(rows), "summary": summary, "per_target": rows,
              "caveat": "Simulated with truthful oracle answers over 7 products; not a user study."}
    out = os.path.join(ROOT, "data", "evaluation", "asksmart_report.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"{'target':16s} {'AskSmart':>9s} {'Fixed':>6s}   AskSmart path")
    for r in rows:
        print(f"{r['target']:16s} {r['asksmart']['questions']:>9d} {r['fixed_baseline']['questions']:>6d}   "
              f"{' → '.join(r['asksmart']['asked'])}{'' if r['asksmart']['resolved'] else '  (unresolved)'}")
    for k, v in summary.items():
        print(f"{k:15s} mean questions = {v['mean_questions']}, resolved {v['resolved']}/{len(rows)}")
    print(f"Report written to {out}")


if __name__ == "__main__":
    main()
