"""M1–M3 — Need Fingerprint revisions, AskSmart, hard constraints and counterfactuals."""

import unittest

from helpers import STORE
from app.decision.fit import FitEngine
from app.dialogue.need_fingerprint import NeedFingerprint
from app.dialogue.question_value import QuestionValueEngine, score_question, QUESTIONS


class TestNeedFingerprint(unittest.TestCase):
    def test_initial_state(self):
        fp = NeedFingerprint("c1")
        self.assertEqual(fp.revision, 0)
        self.assertEqual(fp.known(), {})

    def test_update_has_provenance(self):
        fp = NeedFingerprint("c1")
        fp.update_slot("budget", 5000, "m1", "budget 5000", 0.95)
        s = fp.slots["budget"]
        self.assertEqual((s["value"], s["status"], s["source_message_id"]), (5000, "KNOWN", "m1"))
        self.assertEqual(fp.revision, 1)

    def test_correction_supersedes_never_deletes(self):
        fp = NeedFingerprint("c1")
        fp.update_slot("trolley_configuration", "backpack", "m1")
        fp.update_slot("trolley_configuration", "sidepack", "m2", is_correction=True)
        s = fp.slots["trolley_configuration"]
        self.assertEqual(s["value"], "sidepack")
        self.assertEqual(s["supersedes"]["value"], "backpack")
        self.assertEqual(s["supersedes"]["status"], "SUPERSEDED")
        self.assertEqual([h["type"] for h in fp.history], ["set", "correction"])
        self.assertEqual(fp.contradictions[0]["old_value"], "backpack")

    def test_repeat_value_is_not_a_revision(self):
        fp = NeedFingerprint("c1")
        fp.update_slot("budget", 5000, "m1")
        self.assertIsNone(fp.update_slot("budget", 5000, "m2"))
        self.assertEqual(fp.revision, 1)


class TestAskSmart(unittest.TestCase):
    def _q(self, slot):
        return next(q for q in QUESTIONS if q["slot"] == slot)

    def test_family_question_first_for_unknown_need(self):
        fp = NeedFingerprint("c")
        best = QuestionValueEngine().select_next(fp, STORE.get_all_products())
        self.assertEqual(best["slot"], "product_family")
        self.assertGreater(best["information_gain"], 1.5)

    def test_attachment_vs_machine_beats_configuration(self):
        pool = [STORE.get_product(p) for p in ("BCT-SIDE", "BCT-BACK", "VARSHA-TROLLEY")]
        need = score_question(self._q("need_type"), pool)
        cfg = score_question(self._q("trolley_configuration"), pool)
        self.assertGreater(need["value"], cfg["value"])

    def test_dumper_question_for_wheel_barrows(self):
        fp = NeedFingerprint("c")
        fp.update_slot("product_family", "wheel_barrow", "m1")
        best = QuestionValueEngine().select_next(fp, STORE.get_family("wheel_barrow"))
        self.assertEqual(best["slot"], "dumper_required")

    def test_no_value_when_one_candidate(self):
        s = score_question(self._q("budget"), [STORE.get_product("WB-DUMPER")])
        self.assertEqual(s["value"], 0.0)

    def test_known_slot_not_asked(self):
        fp = NeedFingerprint("c")
        fp.update_slot("product_family", "wheel_barrow", "m1")
        fp.update_slot("dumper_required", True, "m2")
        scored = QuestionValueEngine().score_all(fp, STORE.get_family("wheel_barrow"))
        self.assertNotIn("dumper_required", [s["slot"] for s in scored])


class TestFitEngine(unittest.TestCase):
    def setUp(self):
        self.fit = FitEngine(STORE)

    def _fp(self, **slots):
        fp = NeedFingerprint("c")
        for k, v in slots.items():
            fp.update_slot(k, v, "m")
        return fp

    def test_hard_fail_never_eligible(self):
        rec = self.fit.recommend(self._fp(product_family="wheel_barrow", dumper_required=True, budget=8000))
        self.assertEqual(rec["eligible"], [])
        self.assertIn("WB-DUMPER", rec["excluded"])

    def test_configuration_gate(self):
        rec = self.fit.recommend(self._fp(product_family="brush_cutter_trolley_attachment",
                                          need_type="attachment", trolley_configuration="backpack"))
        self.assertEqual(rec["eligible"], ["BCT-BACK"])

    def test_warranty_gate(self):
        rec = self.fit.recommend(self._fp(product_family="brush_cutter_machine", warranty_min_months=6))
        self.assertEqual(sorted(rec["eligible"]), ["143R-STABLE", "143R-WALBRO"])
        self.assertIn("VARSHA-TROLLEY", rec["excluded"])

    def test_capacity_requirement_unverified(self):
        rec = self.fit.recommend(self._fp(product_family="wheel_barrow", capacity_required_litres=95))
        self.assertEqual(sorted(rec["unverified"]), ["WB-DUMPER", "WB-NORMAL"])
        self.assertEqual(rec["eligible"], [])

    def test_exact_fitment_required_is_unverified(self):
        rec = self.fit.recommend(self._fp(product_family="brush_cutter_trolley_attachment",
                                          trolley_configuration="sidepack", exact_fitment_required=True))
        self.assertEqual(rec["unverified"], ["BCT-SIDE"])

    def test_counterfactual_is_hypothetical_and_specific(self):
        fp = self._fp(product_family="wheel_barrow", dumper_required=True, budget=8000)
        rev = fp.revision
        rec = self.fit.recommend(fp)
        dumper = next(e for e in rec["evaluations"] if e["product_id"] == "WB-DUMPER")
        cf = dumper["counterfactual"]
        self.assertTrue(cf["hypothetical"])
        self.assertIn("8,500", cf["would_qualify"])
        self.assertEqual(fp.revision, rev)          # never mutates the fingerprint

    def test_no_fake_percentage_without_preferences(self):
        rec = self.fit.recommend(self._fp(product_family="wheel_barrow", dumper_required=True))
        self.assertIsNone(rec["evaluations"][0]["preference_fit"])

    def test_preference_ranking_carburetor(self):
        rec = self.fit.recommend(self._fp(product_family="brush_cutter_machine", needs_push_trolley=False,
                                          carburetor_preference="walbro"))
        self.assertEqual(rec["eligible"][0], "143R-WALBRO")


if __name__ == "__main__":
    unittest.main()
