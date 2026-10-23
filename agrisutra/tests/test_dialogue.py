import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import unittest
from app.dialogue.need_fingerprint import NeedFingerprint, FieldStatus
from app.dialogue.question_value import QuestionValueEngine
from app.catalogue.ingest import CatalogueStore


class TestDialogueComponents(unittest.TestCase):
    def setUp(self):
        cat_path = os.path.join(os.path.dirname(__file__), "..", "data", "catalogue", "sample_catalogue.json")
        self.store = CatalogueStore(cat_path)
        self.fp = NeedFingerprint("session-test-fp")
        self.q_engine = QuestionValueEngine(self.store)

    def test_fingerprint_initial_state(self):
        self.assertEqual(self.fp.get_completion_ratio(), 0.0)
        known = self.fp.get_known_fields()
        self.assertEqual(len(known), 0)

    def test_fingerprint_update_provenance(self):
        change = self.fp.update_field("budget", 4000, "msg-001")
        self.assertEqual(change["field"], "budget")
        self.assertEqual(change["new_value"], 4000)
        self.assertEqual(self.fp.requirements["budget"]["status"], FieldStatus.KNOWN)
        self.assertEqual(self.fp.requirements["budget"]["source"], "msg-001")

    def test_fingerprint_correction_history(self):
        self.fp.update_field("budget", 5000, "msg-001")
        change = self.fp.update_field("budget", 3000, "msg-002", is_correction=True)
        self.assertEqual(change["type"], "correction")
        self.assertEqual(change["old_value"], 5000)
        self.assertEqual(change["new_value"], 3000)
        self.assertGreaterEqual(len(self.fp.history), 1)

    def test_question_value_calculation(self):
        candidates = self.store.get_sprayer_products()
        # When all fields unknown, farm size or budget should have high discriminating power
        q = self.q_engine.select_next_question(self.fp, candidates)
        self.assertIsNotNone(q)
        self.assertIn(q["field"], ["budget", "farm_size_acres", "preferred_type", "intended_use"])
        self.assertGreater(q["value_score"], 0.0)

    def test_question_value_reduces_when_field_known(self):
        self.fp.update_field("farm_size_acres", 5.0, "msg-001")
        candidates = self.store.get_sprayer_products()
        all_q = self.q_engine.get_all_scored_questions(self.fp, candidates)
        # farm_size_acres should be filtered or have 0 score because it's already known
        farm_q = [q for q in all_q if q["field"] == "farm_size_acres"]
        self.assertEqual(len(farm_q), 0)


if __name__ == "__main__":
    unittest.main()
