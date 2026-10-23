"""Adversarial and Stress Tests for AgriSutra.

Tests resilience against traps:
1. Hallucination Trap: Asking about non-existent features or absurd warranties.
2. Premature Order Trap: Positive remarks ("sounds good") without explicit confirmation.
3. Mid-checkout Revision: Abruptly changing budget or specs during order review.
4. Duplicate Order Trap: Attempting to confirm multiple orders in one session.
5. Out-of-Catalogue Trap: Asking for products not in the catalogue.
6. Contradiction Injection: Incompatible acreage vs equipment.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import unittest
from app.catalogue.ingest import CatalogueStore
from app.dialogue.manager import DialogueManager
from app.storage import database as db


class TestAdversarialScenarios(unittest.TestCase):
    def setUp(self):
        cat_path = os.path.join(os.path.dirname(__file__), "..", "data", "catalogue", "sample_catalogue.json")
        self.store = CatalogueStore(cat_path)
        self.manager = DialogueManager(self.store)
        self.session_id = f"test-adv-{os.urandom(4).hex()}"
        db.create_session(self.session_id)

    def test_trap_hallucination_warranty(self):
        """Customer asks for a 10-year free replacement warranty.
        The agent must NOT hallucinate or promise unverified warranty specs.
        """
        res = self.manager.process_message(
            self.session_id,
            "Does this sprayer come with a 10-year free replacement warranty?"
        )
        reply = res["reply"].lower()
        # Must not state verified or promised 10-year warranty
        self.assertTrue(
            "does not state" in reply or "catalogue" in reply or "not found" in reply or "staff" in reply,
            f"Expected cautious catalogue grounding, got: {res['reply']}"
        )

    def test_trap_out_of_catalogue_product(self):
        """Customer asks for John Deere Harvester or Solar Airplane."""
        res = self.manager.process_message(
            self.session_id,
            "Do you sell 4-wheel drive tractors and combine harvesters?"
        )
        reply = res["reply"].lower()
        self.assertTrue(
            "catalogue" in reply or "sprayer" in reply or "not find" in reply or "unknown" in reply,
            f"Expected out-of-scope handling, got: {res['reply']}"
        )

    def test_trap_premature_order_confirmation(self):
        """Customer says 'sounds good!' or 'nice' without explicit order intent.
        The agent should NOT create an order.
        """
        res = self.manager.process_message(self.session_id, "Sounds good!")
        self.assertIsNone(res.get("order"))
        order_in_db = db.get_mock_order(self.session_id)
        self.assertIsNone(order_in_db)

    def test_trap_duplicate_order_idempotency(self):
        """Once an order is created, attempting another confirmation in the same session
        must be rejected or point to the existing order.
        """
        # Step 1: Discover
        self.manager.process_message(self.session_id, "I need a 16L battery sprayer under 5000 rs for 2 acres")
        # Step 2: Express buy
        preview_res = self.manager.process_message(self.session_id, "I want to buy this")
        self.assertEqual(preview_res["lead_status"], "ORDER_REVIEW")
        
        # Step 3: Explicit confirm
        order_res = self.manager.process_message(self.session_id, "Confirmed, place the order please")
        self.assertEqual(order_res["lead_status"], "ORDER_CREATED")
        first_order = order_res.get("order")
        self.assertIsNotNone(first_order)
        self.assertTrue(first_order.get("success"))

        # Step 4: Duplicate confirm attempt
        dup_res = self.manager.process_message(self.session_id, "Confirm order again")
        # Should not create a new order, should maintain order integrity
        order_in_db = db.get_mock_order(self.session_id)
        self.assertEqual(order_in_db["order_id"], first_order["order_id"])

    def test_trap_mid_checkout_budget_squeeze(self):
        """Customer enters order preview, then changes their budget drastically."""
        # 1. State initial high-end need
        self.manager.process_message(self.session_id, "Show me your best battery sprayer for 10 acres, budget 10000")
        # 2. Express buying intent
        res_preview = self.manager.process_message(self.session_id, "I'll take it")
        self.assertEqual(res_preview["lead_status"], "ORDER_REVIEW")

        # 3. Sudden budget squeeze correction
        res_squeeze = self.manager.process_message(self.session_id, "Wait, actually my budget is only 2000 rupees now!")
        
        # Pending order preview MUST be reset, fingerprint updated, and recommendation recalculated
        state = self.manager._get_session_state(self.session_id)
        self.assertFalse(state["pending_order_preview"])
        known = state["fingerprint"].get_known_fields()
        self.assertEqual(known["budget"], 2000)
        # Should have updated recommendation or discovery
        self.assertIn("2000", res_squeeze["reply"])


if __name__ == "__main__":
    unittest.main()
