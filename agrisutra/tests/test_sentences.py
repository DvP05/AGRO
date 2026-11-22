"""Natural-sentence understanding: normaliser and full-conversation regression suite."""

import os
import sys
import unittest

from helpers import ROOT, new_manager
from app.nlu.normalize import normalize
from app.nlu.predict import classify_intent, extract_entities

sys.path.insert(0, os.path.join(ROOT, "scripts"))
import evaluate_sentences  # noqa: E402


class TestNormalize(unittest.TestCase):
    def test_spelling(self):
        self.assertIn("wheel barrow", normalize("I need a whelbarow"))
        self.assertIn("brush cutter", normalize("brushcuter with warrenty"))
        self.assertIn("warranty", normalize("brushcuter with warrenty"))

    def test_money_shorthand(self):
        self.assertIn("15000", normalize("budget 15k"))
        self.assertIn("4000", normalize("around 4 thousand"))
        self.assertIn("150000", normalize("1.5 lakh"))

    def test_hinglish(self):
        self.assertIn("soil", normalize("mitti uthane ke liye"))
        self.assertIn("grass", normalize("ghaas kaatne"))

    def test_inflections_not_mangled(self):
        self.assertIn("confirmed", normalize("confirmed, place it"))
        self.assertIn("compared", normalize("compared to the dumper"))
        self.assertEqual(normalize("3 trolleys"), "3 trolley")      # plural folded on purpose


class TestSentenceUnderstanding(unittest.TestCase):
    def test_use_case_infers_family(self):
        self.assertEqual(extract_entities("I have to shift cement bags every day")["product_family"], "wheel_barrow")
        self.assertEqual(extract_entities("need something to cut weeds in my orchard")["product_family"], "brush_cutter_machine")

    def test_described_configuration(self):
        e = extract_entities("I bought a Honda cutter that I carry on my back, I want to push it on wheels")
        self.assertEqual((e["trolley_configuration"], e["product_family"]),
                         ("backpack", "brush_cutter_trolley_attachment"))

    def test_owning_a_farm_is_not_owning_a_machine(self):
        self.assertNotIn("owns_brush_cutter", extract_entities("I have a 5 acre farm and want a brush cutter"))

    def test_indifference_declines_slot(self):
        e = extract_entities("I don't care much about warranty")
        self.assertEqual(e["declined_slots"], ["warranty_min_months"])
        self.assertNotIn("warranty_min_months", e)

    def test_grown_too_much_is_not_price_objection(self):
        t = "The grass has grown too much, what can help me clear it?"
        self.assertNotEqual(classify_intent(t, extract_entities(t))[0], "RAISE_PRICE_OBJECTION")

    def test_longest_warranty_recommends_walbro(self):
        from helpers import chat, new_session
        r = chat(new_manager(), new_session(), "I want the brush cutter with the longest warranty")[-1]
        self.assertEqual(r["next_action"], "RECOMMEND")
        self.assertEqual(r["candidates"]["evaluations"][0]["product_id"], "143R-WALBRO")

    def test_inferred_family_echoes_customer_words(self):
        from helpers import chat, new_session
        r = chat(new_manager(), new_session(), "I have to move sand and bricks around my site")[-1]
        self.assertIn("moving sand and bricks", r["reply"])

    def test_heldout_sentence_suite(self):
        rep = evaluate_sentences.evaluate()
        failed = [(r["turns"], r["problems"]) for r in rep["results"] if not r["passed"]]
        self.assertEqual(failed, [])


if __name__ == "__main__":
    unittest.main()
