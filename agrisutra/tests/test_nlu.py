import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import unittest
from app.nlu.predict import classify_intent, extract_entities


class TestNLU(unittest.TestCase):
    def test_greeting_intent(self):
        intent, conf = classify_intent("Hello there, good morning!")
        self.assertEqual(intent, "GREETING")
        self.assertGreater(conf, 0.7)

    def test_buying_intent(self):
        intent, conf = classify_intent("I want to buy this sprayer")
        self.assertEqual(intent, "EXPRESS_BUYING_INTENT")

    def test_confirm_order_intent(self):
        intent, conf = classify_intent("Yes, please confirm the order")
        self.assertEqual(intent, "CONFIRM_ORDER")

    def test_correction_intent(self):
        intent, conf = classify_intent("Actually, my budget is only 2000 rupees")
        self.assertEqual(intent, "CORRECT_REQUIREMENT")

    def test_ask_for_human_intent(self):
        intent, conf = classify_intent("I want to speak with a human agent please")
        self.assertEqual(intent, "ASK_FOR_HUMAN")

    def test_cancel_intent(self):
        intent, conf = classify_intent("Cancel this order, I don't want it")
        self.assertEqual(intent, "CANCEL_OR_STOP")

    def test_entity_extraction_budget(self):
        entities = extract_entities("My budget is under 4500 rs", [])
        self.assertEqual(entities.get("budget"), 4500)

    def test_entity_extraction_farm_size(self):
        entities = extract_entities("I have a 10 acre orchard", [])
        self.assertEqual(entities.get("farm_size_acres"), 10.0)

    def test_entity_extraction_sprayer_type(self):
        entities = extract_entities("Show me battery operated sprayers", [])
        self.assertEqual(entities.get("preferred_type"), "Battery Sprayer")

    def test_entity_extraction_correction_flag(self):
        entities = extract_entities("Wait, change that: budget is 3000", [])
        self.assertTrue(entities.get("is_correction"))
        self.assertEqual(entities.get("budget"), 3000)


if __name__ == "__main__":
    unittest.main()
