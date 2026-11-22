"""API contracts: sessions, idempotent messages, bound confirmation, handoff, replay."""

import unittest

import helpers  # noqa: F401
from fastapi.testclient import TestClient

from app.main import app


class TestApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def _session(self):
        return self.client.post("/api/v1/sessions").json()["session_id"]

    def _msg(self, sid, text, key=None):
        r = self.client.post(f"/api/v1/sessions/{sid}/messages", json={"text": text, "client_message_id": key})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_unknown_session_404(self):
        self.assertEqual(self.client.post("/api/v1/sessions/nope/messages", json={"text": "hi"}).status_code, 404)

    def test_message_contract(self):
        sid = self._session()
        d = self._msg(sid, "I need a brush cutter trolley")
        for key in ("conversation_id", "message_id", "state", "state_revision", "reply", "next_action",
                    "fingerprint", "trace_id"):
            self.assertIn(key, d)

    def test_confirmation_bound_to_current_preview(self):
        sid = self._session()
        self._msg(sid, "I already have a backpack brush cutter")
        p1 = self._msg(sid, "I'll take it")["order_preview"]
        p2 = self._msg(sid, "make it 2 pieces")["order_preview"]
        self.assertEqual(p2["version"], 2)
        stale = self.client.post(f"/api/v1/sessions/{sid}/order-confirmation", json={"preview_id": p1["preview_id"]}).json()
        self.assertEqual(stale["next_action"], "STALE_PREVIEW_REJECTED")
        ok = self.client.post(f"/api/v1/sessions/{sid}/order-confirmation", json={"preview_id": p2["preview_id"]}).json()
        self.assertTrue(ok["ok"])
        self.assertEqual(ok["order"]["quantity"], 2)
        again = self.client.post(f"/api/v1/sessions/{sid}/order-confirmation", json={"preview_id": p2["preview_id"]}).json()
        self.assertFalse(again["ok"])
        self.assertEqual(again["order"]["order_id"], ok["order"]["order_id"])

    def test_handoff_pause_and_resume(self):
        sid = self._session()
        self._msg(sid, "I want to talk to a salesperson")
        h = self.client.post(f"/api/v1/sessions/{sid}/handoff", json={"agent": "a1"}).json()
        self.assertEqual(h["handoff"]["status"], "HUMAN_OWNED")
        self.assertIn("transcript", h["handoff"]["context_snapshot"])
        self.assertIsNone(self._msg(sid, "hello?")["reply"])
        r = self.client.post(f"/api/v1/sessions/{sid}/agent-message", json={"agent": "a1", "text": "Hi, Ravi here"})
        self.assertEqual(r.status_code, 200)
        self.client.post(f"/api/v1/sessions/{sid}/resume-bot", json={"agent": "a1"})
        self.assertIsNotNone(self._msg(sid, "I need a wheel barrow")["reply"])

    def test_agent_cannot_reply_without_ownership(self):
        sid = self._session()
        r = self.client.post(f"/api/v1/sessions/{sid}/agent-message", json={"agent": "a1", "text": "hi"})
        self.assertEqual(r.status_code, 409)

    def test_replay_has_versions(self):
        sid = self._session()
        self._msg(sid, "I need a wheel barrow")
        self._msg(sid, "I need easy tipping")
        traces = self.client.get(f"/api/v1/sessions/{sid}/replay").json()["traces"]
        self.assertEqual([t["turn"] for t in traces], [1, 2])
        self.assertTrue(traces[1]["candidates"])
        self.assertTrue(traces[1]["nlu"]["model_version"].startswith("intent-lr-"))

    def test_catalogue_report(self):
        rep = self.client.get("/api/v1/catalogue/report").json()
        self.assertEqual(rep["product_count"], 7)
        flagged = {(f["product_id"], f["field"]) for f in rep["flags"]}
        self.assertTrue({("WB-NORMAL", "capacity"), ("143R-STABLE", "engine_displacement"),
                         ("143R-WALBRO", "artwork_label")} <= flagged)
        self.assertIsNotNone(rep["source_hash"])

    def test_product_images_served(self):
        sid = self._session()
        d = self._msg(sid, "I need a wheel barrow with easy tipping")
        img = d["product_cards"][0]["image"]
        self.assertEqual(img["page"], 2)
        r = self.client.get(img["url"])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "image/jpeg")
        fact = self._msg(sid, "How much does the normal wheel barrow cost?")
        self.assertEqual([i["product_id"] for i in fact["images"]], ["WB-NORMAL"])
        self.assertTrue(fact["receipt"]["images"])


if __name__ == "__main__":
    unittest.main()
