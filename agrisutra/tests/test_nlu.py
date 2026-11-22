"""M1 — local intent model and slot extraction."""

import json
import unittest

import helpers  # noqa: F401  (path + temp DB)
from app.nlu import model as intent_model
from app.nlu.predict import classify_intent, extract_entities


def intent(text):
    return classify_intent(text, extract_entities(text))[0]


class TestIntentSafety(unittest.TestCase):
    def test_positive_signals_are_not_confirmation(self):
        for t in ["looks good", "ok", "sounds good", "send details", "I am interested", "I will take it", "yes"]:
            self.assertNotEqual(intent(t), "CONFIRM_ORDER", t)

    def test_explicit_confirmations(self):
        for t in ["Confirm", "confirm order", "Yes, place the order", "I confirm", "please confirm my order"]:
            self.assertEqual(intent(t), "CONFIRM_ORDER", t)

    def test_confirm_question_is_not_confirmation(self):
        self.assertNotEqual(intent("can you confirm if it fits?"), "CONFIRM_ORDER")

    def test_buying_vs_confirm_distinct(self):
        self.assertEqual(intent("I'll take it"), "EXPRESS_BUYING_INTENT")

    def test_risky_intents(self):
        self.assertEqual(intent("stop messaging me"), "OPT_OUT")
        self.assertEqual(intent("Can I get a discount?"), "REQUEST_DISCOUNT")
        self.assertEqual(intent("I want to talk to a person"), "ASK_FOR_HUMAN")
        self.assertEqual(intent("this is too expensive"), "RAISE_PRICE_OBJECTION")

    def test_fact_and_correction(self):
        self.assertEqual(intent("Is the normal wheel barrow 95 litres?"), "ASK_PRODUCT_FACT")
        self.assertEqual(intent("Will this fit my exact model?"), "ASK_PRODUCT_FACT")
        self.assertEqual(intent("Actually, I have a sidepack machine"), "CORRECT_REQUIREMENT")

    def test_heldout_macro_f1_reported(self):
        """Measured on the frozen held-out set; must stay above the release bar."""
        from sklearn.metrics import f1_score
        with open(intent_model.HELDOUT_PATH, encoding="utf-8") as f:
            data = json.load(f)["examples"]
        preds = [intent(t) for t, _ in data]
        gold = [g for _, g in data]
        self.assertGreaterEqual(f1_score(gold, preds, average="macro", zero_division=0), 0.85)
        false_confirms = [t for (t, g), p in zip(data, preds) if p == "CONFIRM_ORDER" and g != "CONFIRM_ORDER"]
        self.assertEqual(false_confirms, [])

    def test_heldout_not_in_training(self):
        texts, _, _ = intent_model.load_training_data()
        with open(intent_model.HELDOUT_PATH, encoding="utf-8") as f:
            held = {t.lower() for t, _ in json.load(f)["examples"]}
        self.assertEqual(held & set(texts), set())


class TestSlots(unittest.TestCase):
    def test_ambiguous_trolley(self):
        e = extract_entities("I need a brush cutter trolley")
        self.assertEqual(e.get("family_hint"), "trolley_ambiguous")
        self.assertNotIn("product_family", e)

    def test_backpack_owner(self):
        e = extract_entities("I already have a backpack brush cutter")
        self.assertEqual(e["trolley_configuration"], "backpack")
        self.assertEqual(e["need_type"], "attachment")
        self.assertEqual(e["product_family"], "brush_cutter_trolley_attachment")

    def test_complete_machine(self):
        e = extract_entities("I want a brush cutter with trolley")
        self.assertEqual(e["product_family"], "brush_cutter_machine")
        self.assertTrue(e["needs_push_trolley"])

    def test_wheel_barrow_tipping_and_negation(self):
        self.assertTrue(extract_entities("wheelbarrow with easy tipping")["dumper_required"])
        self.assertFalse(extract_entities("I don't need a dumper wheel barrow")["dumper_required"])

    def test_budget_and_quantity(self):
        e = extract_entities("need 2 pieces, budget ₹9,000")
        self.assertEqual(e["budget"], 9000)
        self.assertEqual(e["quantity"], 2)

    def test_units_not_budget(self):
        e = extract_entities("I want 52 cc and 2 hours run time")
        self.assertNotIn("budget", e)

    def test_machine_model(self):
        self.assertEqual(extract_entities("my machine is a Stihl FS120")["machine_model"], "STIHL FS120")

    def test_warranty_requirement(self):
        self.assertEqual(extract_entities("I need 1 year warranty")["warranty_min_months"], 12)
        self.assertNotIn("warranty_min_months", extract_entities("what is the warranty?"))

    def test_fact_topics(self):
        self.assertIn("exact_fitment", extract_entities("will it fit my honda?")["fact_topics"])
        self.assertIn("stock", extract_entities("is it in stock")["fact_topics"])


if __name__ == "__main__":
    unittest.main()
