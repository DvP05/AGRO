"""M0 — product truth: ingestion, provenance, flags and evidence statuses."""

import unittest

from helpers import STORE
from app.catalogue.evidence import EvidenceGate
from app.catalogue.retrieval import CatalogueRetriever


class TestCatalogueIngestion(unittest.TestCase):
    def test_seven_supplied_variants(self):
        self.assertEqual(sorted(STORE.products), sorted(
            ["BCT-SIDE", "BCT-BACK", "WB-DUMPER", "WB-NORMAL", "VARSHA-TROLLEY", "143R-STABLE", "143R-WALBRO"]))

    def test_listed_prices_match_source(self):
        expected = {"BCT-SIDE": 3800, "BCT-BACK": 4300, "WB-DUMPER": 8500, "WB-NORMAL": 7500,
                    "VARSHA-TROLLEY": 14000, "143R-STABLE": 16000, "143R-WALBRO": 21000}
        for pid, price in expected.items():
            self.assertEqual(STORE.get_product(pid)["price"]["amount"], price)

    def test_every_fact_has_source_page(self):
        for p in STORE.get_all_products():
            for key, fact in p["facts"].items():
                self.assertTrue(fact["source"]["page"], f"{p['product_id']}.{key} lacks a page")
        self.assertEqual([w for w in STORE.warnings if "no source page" in w], [])

    def test_version_is_content_hash(self):
        self.assertRegex(STORE.version, r"^cat-[0-9a-f]{12}$")

    def test_source_anomalies_flagged_not_promoted(self):
        flags = {(f["product_id"], f["field"]) for f in STORE.flags}
        self.assertIn(("WB-NORMAL", "capacity"), flags)
        self.assertIn(("143R-STABLE", "engine_displacement"), flags)
        self.assertIn(("143R-WALBRO", "engine_displacement"), flags)
        self.assertNotIn("capacity", STORE.get_product("WB-NORMAL")["facts"])
        self.assertNotIn("engine_displacement", STORE.get_product("143R-STABLE")["facts"])

    def test_source_pdf_hash_computed(self):
        import hashlib, os
        pdf = os.path.join(os.path.dirname(STORE.path), "source", STORE.source_file)
        self.assertEqual(STORE.source_hash, hashlib.sha256(open(pdf, "rb").read()).hexdigest())
        self.assertFalse(any("SHA-256" in w for w in STORE.warnings))

    def test_every_product_has_a_traceable_image(self):
        import hashlib, os
        root = os.path.abspath(os.path.join(os.path.dirname(STORE.path), "..", ".."))
        for p in STORE.get_all_products():
            img = p["image"]
            self.assertIsNotNone(img, p["product_id"])
            self.assertEqual(img["page"], p["price"]["source"]["page"], p["product_id"])
            data = open(os.path.join(root, "data", img["asset"]), "rb").read()
            self.assertEqual(hashlib.sha256(data).hexdigest(), img["sha256"])
        self.assertFalse(any("different PDF version" in w for w in STORE.warnings))

    def test_artwork_only_details_are_not_facts(self):
        self.assertNotIn("engine_type", STORE.get_product("VARSHA-TROLLEY")["facts"])
        self.assertNotIn("push_trolley", STORE.get_product("143R-WALBRO")["facts"])
        self.assertIsNotNone(STORE.get_observation("143R-WALBRO", "artwork_label"))


class TestEvidenceGate(unittest.TestCase):
    def setUp(self):
        self.gate = EvidenceGate(STORE)

    def test_supported_fact_has_provenance(self):
        c = self.gate.resolve("143R-WALBRO", "engine_warranty")
        self.assertEqual(c["evidence_status"], "SUPPORTED")
        self.assertEqual(c["rendered_value"], "1 year")
        self.assertEqual(c["source_page"], 6)
        self.assertEqual(c["catalogue_version"], STORE.version)

    def test_seo_capacity_is_unapproved(self):
        c = self.gate.resolve("WB-NORMAL", "capacity")
        self.assertEqual(c["evidence_status"], "UNAPPROVED")
        self.assertIsNone(c["rendered_value"])
        self.assertFalse(self.gate.statable(c))

    def test_artwork_cc_is_conflicted(self):
        c = self.gate.resolve("143R-STABLE", "engine_displacement")
        self.assertEqual(c["evidence_status"], "CONFLICTED")
        self.assertIsNone(c["rendered_value"])

    def test_varsha_cc_supported(self):
        self.assertEqual(self.gate.resolve("VARSHA-TROLLEY", "engine_displacement")["rendered_value"], "52 cc")

    def test_commercial_facts_missing(self):
        for field in ("stock", "delivery", "gst", "payment", "exact_fitment", "load_capacity"):
            self.assertEqual(self.gate.resolve("WB-DUMPER", field)["evidence_status"], "MISSING", field)

    def test_universal_seo_tag_does_not_become_fitment(self):
        c = self.gate.resolve("BCT-SIDE", "exact_fitment")
        self.assertEqual(c["evidence_status"], "MISSING")
        self.assertIn("marketing tag", c["note"])

    def test_trolley_warranty_not_stated_is_missing_not_no(self):
        c = self.gate.resolve("BCT-SIDE", "warranty")
        self.assertEqual(c["evidence_status"], "MISSING")

    def test_runtime_keeps_as_provided_qualifier(self):
        c = self.gate.resolve("VARSHA-TROLLEY", "run_time")
        self.assertEqual(c["qualifier"], "as provided")

    def test_price_marked_not_production_approved(self):
        c = self.gate.resolve("BCT-BACK", "price")
        self.assertEqual(c["approval_status"], "SOURCE_TRANSCRIBED_NOT_PRODUCTION_APPROVED")


class TestRetrieval(unittest.TestCase):
    def test_alias_and_misspelling(self):
        r = CatalogueRetriever(STORE)
        self.assertEqual(r.get_product_for_query("backpack trolly")["product_id"], "BCT-BACK")
        self.assertEqual(r.get_product_for_query("dumper wheelbarrow")["product_id"], "WB-DUMPER")

    def test_bm25_ranks_walbro(self):
        r = CatalogueRetriever(STORE)
        self.assertEqual(r.search("walbro carburetor brush cutter")[0]["product_id"], "143R-WALBRO")


if __name__ == "__main__":
    unittest.main()
