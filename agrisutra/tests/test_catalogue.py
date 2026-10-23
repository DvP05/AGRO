import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import unittest
from app.catalogue.ingest import CatalogueStore
from app.catalogue.retrieval import CatalogueRetriever
from app.catalogue.evidence import EvidenceGate, EvidenceStatus


class TestCatalogueStore(unittest.TestCase):
    def setUp(self):
        cat_path = os.path.join(os.path.dirname(__file__), "..", "data", "catalogue", "sample_catalogue.json")
        self.store = CatalogueStore(cat_path)

    def test_catalogue_loads_products(self):
        self.assertGreater(len(self.store.products), 0)
        self.assertIn("SGB-SPR-001", self.store.products)
        p1 = self.store.get_product("SGB-SPR-001")
        self.assertEqual(p1["name"], "AgriMax 16L Manual Knapsack Sprayer")

    def test_safe_field_access(self):
        price = self.store.get_field_value("SGB-SPR-001", "price.amount")
        self.assertEqual(price, 1850)
        missing = self.store.get_field_value("SGB-SPR-001", "non_existent.field")
        self.assertIsNone(missing)

    def test_unknown_product_returns_none(self):
        p = self.store.get_product("NON-EXISTENT-ID")
        self.assertIsNone(p)


class TestCatalogueRetriever(unittest.TestCase):
    def setUp(self):
        cat_path = os.path.join(os.path.dirname(__file__), "..", "data", "catalogue", "sample_catalogue.json")
        self.store = CatalogueStore(cat_path)
        self.retriever = CatalogueRetriever(self.store)

    def test_retrieval_returns_relevant_products(self):
        results = self.retriever.search("battery sprayer", top_k=3)
        self.assertGreater(len(results), 0)
        self.assertIn("battery", results[0]["name"].lower())

    def test_faq_search(self):
        results = self.retriever.search_faq("Can I spray liquid fertiliser?", top_k=2)
        self.assertGreater(len(results), 0)
        self.assertTrue(any("fertiliser" in r.get("answer", "").lower() for r in results))


class TestEvidenceGate(unittest.TestCase):
    def setUp(self):
        cat_path = os.path.join(os.path.dirname(__file__), "..", "data", "catalogue", "sample_catalogue.json")
        self.store = CatalogueStore(cat_path)
        self.evidence_gate = EvidenceGate(self.store)

    def test_verify_known_claim(self):
        claims = [
            {"claim_type": "price", "field_path": "price.amount", "stated_value": 1850}
        ]
        receipt = self.evidence_gate.create_receipt("SGB-SPR-001", claims)
        self.assertEqual(receipt["status"], EvidenceStatus.VERIFIED)
        self.assertEqual(len(receipt["claims"]), 1)
        self.assertEqual(receipt["claims"][0]["status"], EvidenceStatus.VERIFIED)

    def test_detect_conflict_claim(self):
        claims = [
            {"claim_type": "price", "field_path": "price.amount", "stated_value": 99999}
        ]
        receipt = self.evidence_gate.create_receipt("SGB-SPR-001", claims)
        self.assertEqual(receipt["status"], EvidenceStatus.CONFLICT)

    def test_detect_missing_field(self):
        claims = [
            {"claim_type": "warranty", "field_path": "warranty.unlimited_free_replacement", "stated_value": "yes"}
        ]
        receipt = self.evidence_gate.create_receipt("SGB-SPR-001", claims)
        self.assertEqual(receipt["status"], EvidenceStatus.MISSING)


if __name__ == "__main__":
    unittest.main()
