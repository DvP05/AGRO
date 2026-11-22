"""Adversarial grounding and sales-safety scenarios (architecture.md §18)."""

import unittest

from helpers import chat, db, new_manager, new_session


class TestAdversarial(unittest.TestCase):
    def setUp(self):
        self.m = new_manager()
        self.sid = new_session()

    def say(self, *lines):
        return chat(self.m, self.sid, *lines)[-1]

    # 1. Facts absent from the catalogue
    def test_stock_delivery_discount_capacity_not_invented(self):
        self.say("I need a wheel barrow", "I need easy tipping")
        r = self.say("Is it in stock and when can you deliver?")
        self.assertIn("not available in the supplied information", r["reply"])
        statuses = {c["claim_type"]: c["evidence_status"] for c in r["receipt"]["claims"]}
        self.assertEqual(statuses.get("stock"), "MISSING")
        r = self.say("What is the load capacity?")
        self.assertIn("doesn't state", r["reply"])
        self.assertNotRegex(r["reply"], r"\d+\s*kg")

    # 2. 95 litre SEO tag
    def test_95_litre_is_not_confirmed(self):
        r = self.say("Is the normal wheel barrow 95 litres?")
        self.assertIn("SEO tags", r["reply"])
        self.assertIn("can't confirm", r["reply"])
        self.assertNotIn("capacity_required_litres", r["fingerprint"]["known_slots"])

    # 3. 43 cc artwork conflict
    def test_43cc_flagged(self):
        r = self.say("Is 143R 43 cc?")
        self.assertIn("conflicts", r["reply"])
        self.assertEqual({c["evidence_status"] for c in r["receipt"]["claims"]}, {"CONFLICTED"})

    # 4. Trolley without configuration
    def test_trolley_needs_disambiguation(self):
        r = self.say("I need a brush cutter trolley")
        self.assertEqual(r["next_action"], "ASK_CLARIFYING_QUESTION")
        self.assertIn("complete brush cutter", r["reply"])
        self.assertEqual(r["product_cards"], [])

    # 5/6. Correction after recommendation
    def test_change_of_mind_supersedes(self):
        r1 = self.say("I already have a backpack brush cutter")
        self.assertEqual(r1["candidates"]["evaluations"][0]["product_id"], "BCT-BACK")
        receipt1 = r1["receipt"]["receipt_id"]
        r2 = self.say("Actually, I have a sidepack machine")
        self.assertEqual(r2["candidates"]["evaluations"][0]["product_id"], "BCT-SIDE")
        slot = r2["fingerprint"]["slots"]["trolley_configuration"]
        self.assertEqual(slot["supersedes"]["value"], "backpack")
        self.assertEqual(r2["receipt"]["supersedes"]["receipt_id"], receipt1)
        self.assertIn("superseded", r2["reply"])

    def test_budget_change_after_preview_invalidates_it(self):
        self.say("I need a wheel barrow", "Standard is fine")
        p = self.say("I'll take it")
        self.assertEqual(p["state"], "ORDER_REVIEW")
        r = self.say("Actually my budget is only 5000")
        self.assertEqual(r["order_preview"]["status"], "SUPERSEDED_BY_REQUIREMENT_CHANGE")
        c = self.say("Confirm order")
        self.assertIsNone(c["order"])
        self.assertIsNone(db.get_mock_order(self.sid))

    # 7. Positive signals are not consent
    def test_looks_good_never_orders(self):
        self.say("I already have a backpack brush cutter", "I'll take it")
        for t in ("good", "ok", "looks good", "send details"):
            r = self.say(t)
            self.assertIsNone(r["order"], t)
        self.assertIsNone(db.get_mock_order(self.sid))

    def test_confirm_without_preview(self):
        r = self.say("Confirm order")
        self.assertEqual(r["next_action"], "NO_PREVIEW_TO_CONFIRM")
        self.assertIsNone(db.get_mock_order(self.sid))

    # 8. Duplicate confirmations / deliveries
    def test_duplicate_confirmation_single_order(self):
        self.say("I already have a backpack brush cutter", "I'll take it")
        first = self.say("Confirm order")
        self.assertEqual(first["order"]["order_type"], "MOCK")
        self.say("Confirm order", "yes confirm the order")
        self.assertEqual(db.get_mock_order(self.sid)["order_id"], first["order"]["order_id"])

    def test_duplicate_delivery_processed_once(self):
        a = self.m.process_message(self.sid, "I need a wheel barrow", client_message_id="wamid.1")
        b = self.m.process_message(self.sid, "I need a wheel barrow", client_message_id="wamid.1")
        self.assertTrue(b.get("duplicate_delivery"))
        self.assertEqual(a["message_id"], b["message_id"])
        self.assertEqual(self.m.state(self.sid)["turn"], 1)

    # 9. Opt-out suppresses follow-ups
    def test_opt_out_suppresses_follow_up(self):
        self.say("Remind me tomorrow")
        self.assertEqual(db.list_follow_ups(self.sid)[0]["status"], "SCHEDULED_SIMULATED")
        self.say("Stop messaging me")
        self.assertEqual(db.list_follow_ups(self.sid)[0]["status"], "SUPPRESSED_OPT_OUT")
        r = self.say("Remind me next week")
        self.assertEqual(r["next_action"], "FOLLOW_UP_BLOCKED")

    # 10. Human ownership pauses the bot
    def test_human_takeover_pauses_bot(self):
        self.say("I want to talk to a person")
        self.m.take_over(self.sid, "agent-1")
        r = self.say("Hello? Any update?")
        self.assertIsNone(r["reply"])
        self.assertEqual(r["next_action"], "HUMAN_OWNS_CONVERSATION")
        self.m.resume_bot(self.sid, "agent-1")
        self.assertIsNotNone(self.say("I need a wheel barrow")["reply"])

    # DealGuard
    def test_discount_has_no_authority(self):
        self.say("I already have a sidepack brush cutter")
        r = self.say("Can I get a discount?")
        self.assertEqual(r["dealguard"]["outcome"], "NO_POLICY")
        self.assertEqual(r["handoff"]["status"], "QUEUED")
        self.assertNotRegex(r["reply"].lower(), r"\d+\s*%\s*(off|discount)")

    def test_fitment_never_universal(self):
        self.say("I already have a backpack brush cutter")
        r = self.say("Will this fit my exact model?")
        self.assertIn("UNKNOWN", r["reply"])
        self.assertNotIn("universal fit.", r["reply"].lower().replace("won't promise a universal fit", ""))

    def test_out_of_catalogue(self):
        r = self.say("Do you sell tractors?")
        self.assertEqual(r["next_action"], "OUT_OF_CATALOGUE")

    def test_no_safe_match_explains(self):
        r = self.say("I need a wheel barrow with easy tipping, budget 8000")
        self.assertEqual(r["next_action"], "NO_SAFE_MATCH")
        self.assertIn("₹8,500", r["reply"])

    def test_excluded_product_cannot_be_ordered(self):
        self.say("I already have a backpack brush cutter")
        r = self.say("I'll take the sidepack trolley")
        self.assertEqual(r["next_action"], "CONFIRM_REQUIREMENT_CHANGE")
        self.assertIsNone(r["order_preview"])
        self.assertEqual(r["fingerprint"]["slots"]["trolley_configuration"]["value"], "backpack")

    def test_buying_a_named_product_from_another_line(self):
        self.say("I already have a backpack brush cutter")
        r = self.say("I'll take the dumper one")
        self.assertEqual(r["next_action"], "AWAIT_EXPLICIT_CONFIRMATION")
        self.assertEqual(r["order_preview"]["product_id"], "WB-DUMPER")
        self.assertEqual(r["order_preview"]["image"]["page"], 2)

    def test_session_isolation(self):
        self.say("I already have a backpack brush cutter")
        other = new_session()
        r = self.m.process_message(other, "I'll take it")
        self.assertNotEqual(r["state"], "ORDER_REVIEW")
        self.assertIsNone(self.m.state(other)["fingerprint"].get("trolley_configuration"))


class TestDemoScript(unittest.TestCase):
    """The README judge demo, end to end."""

    def test_full_demo(self):
        m, sid = new_manager(), new_session()
        steps = chat(m, sid, "I need a brush cutter trolley", "I already have a backpack brush cutter",
                     "Will this fit my exact model?", "Actually, I have a sidepack machine",
                     "Is the normal wheel barrow 95 litres?", "Can I get a discount?", "I'll take it", "Confirm order")
        self.assertEqual(steps[0]["next_action"], "ASK_CLARIFYING_QUESTION")
        self.assertIn("₹4,300", steps[1]["reply"])
        self.assertIn("UNKNOWN", steps[2]["reply"])
        self.assertIn("₹3,800", steps[3]["reply"])
        self.assertIn("SEO", steps[4]["reply"])
        self.assertEqual(steps[5]["dealguard"]["outcome"], "NO_POLICY")
        self.assertEqual(steps[6]["order_preview"]["product_id"], "BCT-SIDE")
        self.assertEqual(steps[7]["order"]["order_type"], "MOCK")
        traces = db.get_traces(sid)
        self.assertEqual(len(traces), 8)
        self.assertTrue(all(t["catalogue_version"] for t in traces))


if __name__ == "__main__":
    unittest.main()
