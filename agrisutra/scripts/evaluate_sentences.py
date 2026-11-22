"""Run the held-out natural-sentence conversations through the full dialogue manager.

Usage:  python scripts/evaluate_sentences.py   (writes data/evaluation/sentences_report.json)
"""

import json
import os
import sys
import tempfile
import uuid

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.storage import database as db  # noqa: E402

if __name__ == "__main__":
    db.DB_PATH = os.path.join(tempfile.mkdtemp(), "sentences.db")

from app.catalogue.ingest import CatalogueStore  # noqa: E402
from app.dialogue.manager import DialogueManager  # noqa: E402

CASES_PATH = os.path.join(ROOT, "data", "evaluation", "sentences_heldout.json")


def run_case(m: DialogueManager, case: dict) -> dict:
    sid = f"sent-{uuid.uuid4().hex[:8]}"
    db.create_session(sid)
    for t in case["turns"]:
        r = m.process_message(sid, t)
    known = {k: r["fingerprint"]["slots"][k]["value"] for k in r["fingerprint"]["known_slots"]}
    problems = []
    for k, v in case.get("expect", {}).items():
        if known.get(k) != v:
            problems.append(f"{k}: expected {v!r}, got {known.get(k)!r}")
    if case.get("intent") and r["intent"] != case["intent"]:
        problems.append(f"intent: expected {case['intent']}, got {r['intent']}")
    if case.get("top"):
        evals = [e for e in (r["candidates"] or {}).get("evaluations", []) if e["status"] != "EXCLUDED"]
        top = evals[0]["product_id"] if evals else None
        if top != case["top"]:
            problems.append(f"top product: expected {case['top']}, got {top}")
    if case.get("not_asked"):
        lq = (m.state(sid).get("last_question") or {}).get("slot")
        if r["next_action"] == "ASK_CLARIFYING_QUESTION" and lq == case["not_asked"]:
            problems.append(f"asked about {case['not_asked']} after customer said it doesn't matter")
    if case.get("not_slot") and case["not_slot"] in known:
        problems.append(f"{case['not_slot']} wrongly captured as {known[case['not_slot']]!r}")
    return {"turns": case["turns"], "passed": not problems, "problems": problems,
            "intent": r["intent"], "next_action": r["next_action"], "known": known}


def evaluate() -> dict:
    with open(CASES_PATH, encoding="utf-8") as f:
        cases = json.load(f)["cases"]
    m = DialogueManager(CatalogueStore(os.path.join(ROOT, "data", "catalogue", "sgb_catalogue.json")))
    results = [run_case(m, c) for c in cases]
    return {"n": len(results), "passed": sum(r["passed"] for r in results), "results": results}


if __name__ == "__main__":
    rep = evaluate()
    out = os.path.join(ROOT, "data", "evaluation", "sentences_report.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=2, ensure_ascii=False)
    for r in rep["results"]:
        mark = "✓" if r["passed"] else "✗"
        print(f"{mark} {' || '.join(r['turns'])}")
        for p in r["problems"]:
            print(f"     {p}   [intent={r['intent']} action={r['next_action']}]")
    print(f"\nPassed {rep['passed']}/{rep['n']} held-out sentence conversations. Report: {out}")
