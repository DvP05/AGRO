"""Answering open questions, recommending with a reason, and finishing the sale properly."""

import json
import unittest

from helpers import chat, new_manager, new_session

from app.sales.checkout import parse_cart, parse_contact
from app.storage import database as db


class TestOpenQuestions(unittest.TestCase):
    def setUp(self):
        self.m = new_manager()

    def ask(self, *lines):
        return chat(self.m, new_session(), *lines)[-1]

    def test_capability_is_honest_about_unlisted_uses(self):
        r = self.ask("can the walbro cut sugarcane?")
        self.assertEqual(r["conversation_act"], "CAPABILITY")
        self.assertIn("doesn't mention sugarcane", r["reply"])
        self.assertNotIn(": yes", r["reply"])

    def test_capability_yes_from_described_uses(self):
        r = self.ask("is the dumper good for a construction site?")
        self.assertIn("yes", r["reply"])
        self.assertIn("construction materials", r["reply"])

    def test_wrong_tool_for_the_job(self):
        r = self.ask("can the wheel barrow cut grass?")
        self.assertIn("No, wheel barrows are for carrying", r["reply"])

    def test_superlative_from_facts(self):
        r = self.ask("which brush cutter runs longest")
        self.assertEqual(r["conversation_act"], "SUPERLATIVE")
        self.assertIn("143R WALBRO", r["reply"])
        self.assertIn("3 hours", r["reply"])

    def test_superlative_ties_and_missing_facts(self):
        self.assertIn("same", self.ask("which is the most powerful brush cutter")["reply"])
        r = self.ask("which brush cutter is lightest")
        self.assertIn("doesn't list the weight", r["reply"])

    def test_ease_of_use_answer(self):
        r = self.ask("can a woman use the brush cutter easily")
        self.assertEqual(r["conversation_act"], "EASE_OF_USE")
        self.assertIn("push-type trolley", r["reply"])

    def test_easy_tipping_is_still_a_requirement(self):
        r = self.ask("I need a wheel barrow with easy tipping")
        self.assertEqual(r["next_action"], "RECOMMEND")

    def test_multi_topic_question(self):
        r = self.ask("price of walbro and its warranty")
        self.assertIn("₹21,000", r["reply"])
        self.assertIn("1 year", r["reply"])

    def test_engine_and_power(self):
        r = self.ask("what engine does walbro have and how much power")
        self.assertIn("2-stroke", r["reply"])
        self.assertIn("2.1 HP", r["reply"])

    def test_run_time_question(self):
        self.assertIn("1.5 hours", self.ask("how long does the varsha run on one tank")["reply"])

    def test_bare_variant_words_pick_the_right_product(self):
        self.assertIn("143R Stable", self.ask("will the stable one work for clearing bushes?")["reply"])
        r = self.ask("is the normal barrow ok for carrying bricks?")
        self.assertIn("Normal Wheel Barrow", r["reply"])
        self.assertNotIn("Dumper", r["reply"])
        self.assertIn("6 months", self.ask("what's the warranty and running time of the stable")["reply"])

    def test_wheels_question(self):
        self.assertIn("push-type trolley", self.ask("does the varsha come with wheels")["reply"])
        r = self.ask("is the walbro supplied with a trolley")
        self.assertEqual(r["next_action"], "ANSWER_WITH_EVIDENCE")       # a question, not a new requirement
        self.assertIn("can't confirm", r["reply"])

    def test_family_member_ease_question(self):
        self.assertEqual(self.ask("can my father who is 65 use the varsha")["conversation_act"], "EASE_OF_USE")

    def test_trolley_included_question_stays_unconfirmed(self):
        r = self.ask("does the 143R stable have a trolley")
        self.assertEqual(r["next_action"], "ANSWER_WITH_EVIDENCE")
        self.assertIn("can't confirm", r["reply"])


class TestRecommendationReasons(unittest.TestCase):
    def setUp(self):
        self.m = new_manager()

    def test_long_hours_picks_longest_running(self):
        r = chat(self.m, new_session(), "best brush cutter for long hours of work")[0]
        self.assertEqual(r["candidates"]["evaluations"][0]["product_id"], "143R-WALBRO")
        self.assertIn("My pick is **143R WALBRO", r["reply"])

    def test_large_area_infers_long_running(self):
        r = chat(self.m, new_session(), "which one should i buy for 5 acres of grass")[0]
        self.assertEqual(r["candidates"]["evaluations"][0]["product_id"], "143R-WALBRO")

    def test_budget_reason(self):
        r = chat(self.m, new_session(), "i need to carry soil, budget 8000")[0]
        self.assertIn("within your budget", r["reply"])


class TestCheckout(unittest.TestCase):
    def setUp(self):
        self.m = new_manager()
        self.sid = new_session()

    def test_parse_cart(self):
        cart = dict(parse_cart("i need 2 dumper barrows and 1 sidepack trolley", self.m.alias_index))
        self.assertEqual(cart, {"WB-DUMPER": 2, "BCT-SIDE": 1})
        self.assertEqual(parse_cart("i need 2 wheel barrows", self.m.alias_index), [])

    def test_parse_contact(self):
        self.assertEqual(parse_contact("I'm Ramesh from Mandya"), {"name": "Ramesh", "area": "Mandya"})
        self.assertEqual(parse_contact("Ravi, Tumkur"), {"name": "Ravi", "area": "Tumkur"})
        self.assertEqual(parse_contact("skip"), "SKIP")
        self.assertIsNone(parse_contact("is it in stock?"))
        self.assertIsNone(parse_contact("i am interested"))

    def test_cart_to_finalised_order(self):
        rs = chat(self.m, self.sid, "i need 2 dumper barrows and 1 sidepack trolley", "make the dumper 3",
                  "confirm order", "I'm Ramesh from Mandya", "what happens next")
        self.assertEqual(rs[0]["order_preview"]["line_total"], 20800)
        self.assertIsNone(rs[0]["fingerprint"]["slots"]["quantity"]["value"])   # cart lines aren't the "need"
        self.assertEqual(rs[1]["order_preview"]["line_total"], 29300)
        self.assertEqual(rs[2]["next_action"], "ORDER_CONFIRMED")
        self.assertIn("town or district", rs[2]["reply"])
        self.assertEqual(rs[3]["next_action"], "ORDER_FINALISED")
        self.assertIn("All set, Ramesh", rs[3]["reply"])
        self.assertIn("Mandya", rs[3]["reply"])
        self.assertEqual(rs[3]["handoff"]["reason"], "ORDER_FOLLOW_UP")
        self.assertEqual(rs[4]["next_action"], "ORDER_STATUS")
        order = db.get_mock_order(self.sid)
        self.assertEqual(order["details"]["contact"], {"name": "Ramesh", "area": "Mandya"})
        self.assertEqual(len(order["details"]["items"]), 2)

    def test_contact_never_in_audit_log(self):
        chat(self.m, self.sid, "i want the walbro", "confirm order", "I'm Ramesh from Mandya")
        events = json.dumps(db.get_events(self.sid))
        self.assertNotIn("Ramesh", events)
        self.assertNotIn("Mandya", events)

    def test_two_step_contact_and_skip(self):
        rs = chat(self.m, self.sid, "i want the walbro", "confirm order", "Suresh", "Hassan district")
        self.assertEqual(rs[2]["next_action"], "ASK_CONTACT")
        self.assertIn("Delivery area: Hassan", rs[3]["reply"])
        rs = chat(self.m, new_session(), "i want the walbro", "confirm order", "skip")
        self.assertEqual(rs[2]["next_action"], "ORDER_FINALISED")

    def test_question_after_order_is_not_swallowed(self):
        rs = chat(self.m, self.sid, "i want the walbro", "confirm order", "does it have a 1 year warranty?")
        self.assertNotEqual(rs[2]["next_action"], "ORDER_FINALISED")
        self.assertIn("1 year", rs[2]["reply"])

    def test_question_then_buy_it(self):
        rs = chat(self.m, self.sid, "which brush cutter has the best warranty?", "can it cut sugarcane?",
                  "ok I'll take 2", "confirm order", "Lakshmi, Mysore")
        self.assertIsNone(rs[1]["fingerprint"]["slots"]["intended_use"]["value"])   # a question isn't a requirement
        self.assertEqual(rs[2]["order_preview"]["product_id"], "143R-WALBRO")
        self.assertEqual(rs[2]["order_preview"]["quantity"], 2)
        self.assertIn("All set, Lakshmi", rs[4]["reply"])

    def test_bare_quantity_on_cart_asks_which_item(self):
        rs = chat(self.m, self.sid, "i need 2 dumper barrows and 1 sidepack trolley", "change to 5")
        self.assertIn("Which one should I change", rs[1]["reply"])


if __name__ == "__main__":
    unittest.main()
