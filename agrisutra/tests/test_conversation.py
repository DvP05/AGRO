"""General conversation: everyday messages get a natural, relevant reply — not the same menu question.

Cases come from two probe rounds. Round 2 (20 phrasings never seen while building the layer) passed
13/20 on first sight before the fixes; see data/evaluation/conversation_heldout.json.
"""

import unittest

from helpers import chat, new_manager, new_session

MENU_LINE = "Which product are you looking for"


class TestConversationalActs(unittest.TestCase):
    def setUp(self):
        self.m = new_manager()

    def one(self, text):
        return chat(self.m, new_session(), text)[0]

    def assertAct(self, text, act):
        r = self.one(text)
        self.assertEqual(r["conversation_act"], act, f"{text!r} → {r['conversation_act']} / {r['reply'][:80]}")
        self.assertNotIn(MENU_LINE, r["reply"])
        return r

    def test_small_talk(self):
        self.assertAct("hello how are you", "HOW_ARE_YOU")
        self.assertAct("kya haal hai", "HOW_ARE_YOU")
        self.assertAct("who are you", "WHO_ARE_YOU")
        self.assertAct("what can you do", "CAPABILITIES")
        self.assertAct("tell me about your company", "ABOUT_COMPANY")
        self.assertAct("nice, very helpful", "PRAISE")

    def test_thanks_does_not_close_the_enquiry(self):
        r = self.assertAct("thanks", "THANKS")
        self.assertNotEqual(r["lead_status"], "CLOSED")
        self.assertAct("thank you so much", "THANKS")

    def test_goodbye(self):
        self.assertAct("ok bye", "GOODBYE")

    def test_are_you_human_is_a_question_not_a_handoff(self):
        r = self.assertAct("are you a real person?", "WHO_ARE_YOU")
        self.assertNotIn("handoff_queued", r.get("side_effects", []))
        self.assertIsNone(r["handoff"])

    def test_explicit_human_request_still_hands_off(self):
        r = self.one("I want to talk to a real person")
        self.assertEqual(r["next_action"], "HANDOFF_QUEUED")

    def test_overview_lists_every_product_with_price(self):
        r = self.assertAct("what all do you guys make", "OVERVIEW")
        for name in ("Normal Wheel Barrow", "Dumper Wheel Barrow", "Varsha", "143R Stable", "143R WALBRO",
                     "Sidepack Trolley", "Backpack Trolley"):
            self.assertIn(name, r["reply"])
        self.assertIn("₹7,500", r["reply"])

    def test_cheapest_and_most_expensive(self):
        self.assertIn("Sidepack Trolley", self.assertAct("which is cheapest", "CHEAPEST")["reply"])
        self.assertIn("143R WALBRO", self.assertAct("which one is the most expensive", "PRICIEST")["reply"])

    def test_logistics_are_honest(self):
        r = self.assertAct("can you ship to pune", "DELIVERY")
        self.assertIn("aren't in the catalogue", r["reply"])
        self.assertAct("do you deliver to karnataka?", "DELIVERY")
        r = self.assertAct("how do I pay", "HOW_TO_BUY")
        self.assertIn("don't take any payment", r["reply"])
        self.assertAct("is it available now", "STOCK")
        self.assertAct("do you have spare parts", "SERVICE")

    def test_out_of_range_items(self):
        r = self.assertAct("i want to spray pesticide on my crops", "OUT_OF_SCOPE")
        self.assertIn("doesn't make sprayers", r["reply"])
        self.assertEqual(self.one("i need a tractor")["next_action"], "OUT_OF_CATALOGUE")

    def test_vague_needs_ask_about_the_job(self):
        for text in ("i am a farmer, i need something for my farm", "i have 2 acres of sugarcane, need help",
                     "mujhe kuch chahiye khet ke liye", "suggest something for my land", "i don't know what i need",
                     "i have a coconut farm"):
            r = self.assertAct(text, "VAGUE_NEED")
            self.assertIn("Cut grass & weeds", r["quick_replies"])
        self.assertIn("coconut farm", self.one("i have a coconut farm")["reply"])

    def test_cheap_for_garden_finds_the_job_first(self):
        r = self.assertAct("i need something cheap for small garden", "VAGUE_NEED")
        self.assertIn("budget", r["reply"])

    def test_broken_machine_is_a_replacement(self):
        r = self.assertAct("my old machine broke, need new one", "REPLACEMENT")
        self.assertNotIn("sidepack", r["reply"].lower())

    def test_idle_acknowledgements_vary(self):
        sid = new_session()
        a, b = chat(self.m, sid, "ok", "yes")
        self.assertEqual(a["conversation_act"], "IDLE_ACK")
        self.assertNotEqual(a["reply"], b["reply"])

    def test_complaint_apologises_and_offers_a_person(self):
        r = self.assertAct("you are useless", "COMPLAINT")
        self.assertIn("Talk to sales team", r["quick_replies"])


class TestConversationInContext(unittest.TestCase):
    def setUp(self):
        self.m = new_manager()
        self.sid = new_session()

    def test_ordinal_after_overview(self):
        r = chat(self.m, self.sid, "what do you sell?", "the second one")[1]
        self.assertEqual(r["conversation_act"], "ORDINAL")
        self.assertIn("Dumper Wheel Barrow", r["reply"])

    def test_opinion_uses_catalogue_facts(self):
        r = chat(self.m, self.sid, "is the walbro one worth the money?")[0]
        self.assertEqual(r["conversation_act"], "OPINION")
        self.assertIn("1 year", r["reply"])
        self.assertIn("engine displacement", r["reply"])    # 43 cc stays unconfirmed even in an opinion
        self.assertNotIn("43 cc", r["reply"])

    def test_cheaper_one_then_preview(self):
        rs = chat(self.m, self.sid, "i want to cut grass", "warranty doesn't matter", "the cheaper one",
                  "show order preview")
        self.assertIn("Varsha", rs[2]["reply"])
        self.assertEqual(rs[3]["next_action"], "AWAIT_EXPLICIT_CONFIRMATION")
        self.assertEqual(rs[3]["order_preview"]["product_id"], "VARSHA-TROLLEY")

    def test_weed_problem_infers_brush_cutter(self):
        for text in ("my farm has lot of weeds, what should i use", "the weeds in my orchard are out of control"):
            r = chat(self.m, new_session(), text)[0]
            self.assertEqual(r["fingerprint"]["slots"]["product_family"]["value"], "brush_cutter_machine", text)

    def test_warranty_without_context_answers_all_machines(self):
        r = chat(self.m, self.sid, "what is the warranty")[0]
        for v in ("no engine warranty", "6 months", "1 year"):
            self.assertIn(v, r["reply"])

    def test_comparison_gives_a_take(self):
        r = chat(self.m, self.sid, "i want to cut grass", "which one is better")[1]
        self.assertIn("My take", r["reply"])

    def test_thanks_after_recommendation_keeps_the_sale_open(self):
        rs = chat(self.m, self.sid, "I need to carry cement", "thanks")
        r = rs[1]
        self.assertEqual(r["conversation_act"], "THANKS")
        self.assertNotEqual(r["lead_status"], "CLOSED")
        self.assertTrue(r["quick_replies"])                   # the open question / next step is re-offered

    def test_cancel_still_cancels(self):
        r = chat(self.m, self.sid, "I need to carry cement", "cancel it, not interested")[1]
        self.assertEqual(r["next_action"], "CANCELLED")

    def test_job_quick_replies_are_understood(self):
        for qr, fam in (("Move soil / loads", "wheel_barrow"), ("Cut grass & weeds", "brush_cutter_machine"),
                        ("I own a brush cutter", "brush_cutter_trolley_attachment")):
            r = chat(self.m, new_session(), "hi", qr)[1]
            self.assertEqual(r["fingerprint"]["slots"]["product_family"]["value"], fam, qr)


if __name__ == "__main__":
    unittest.main()
