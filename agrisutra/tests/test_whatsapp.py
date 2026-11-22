"""WhatsApp channel: webhook verification, signatures, dedupe, formatting, policy gates, bound confirmation."""

import hashlib
import hmac
import json
import os
import time
import unittest
import uuid

import helpers  # noqa: F401  (path + temp DB)
from fastapi.testclient import TestClient

from app.channels.whatsapp import formatter, service, store
from app.main import app, manager
from app.storage import database as db

SECRET = "test-app-secret"
VERIFY = "test-verify-token"


class RecordingTransport:
    """Stands in for the Graph API: records what would be sent."""
    def __init__(self):
        self.sent = []

    def resolve_media(self, msg):
        return msg

    def send(self, to, msg):
        self.sent.append((to, msg))
        return {"status": "SENT", "provider_message_id": f"wamid.out.{uuid.uuid4().hex[:8]}", "error": None}


def envelope(wa_id, message=None, statuses=None, name="Ravi"):
    value = {"messaging_product": "whatsapp", "metadata": {"phone_number_id": "123"},
             "contacts": [{"wa_id": wa_id, "profile": {"name": name}}]}
    if message:
        value["messages"] = [message]
    if statuses:
        value["statuses"] = statuses
    return {"object": "whatsapp_business_account", "entry": [{"id": "waba", "changes": [{"field": "messages", "value": value}]}]}


def text_msg(wa_id, body, mid=None):
    return {"from": wa_id, "id": mid or f"wamid.in.{uuid.uuid4().hex[:10]}", "timestamp": str(int(time.time())),
            "type": "text", "text": {"body": body}}


def button_msg(wa_id, reply_id, title="x", kind="button_reply"):
    return {"from": wa_id, "id": f"wamid.in.{uuid.uuid4().hex[:10]}", "timestamp": str(int(time.time())),
            "type": "interactive", "interactive": {"type": kind, kind: {"id": reply_id, "title": title}}}


class TestWhatsAppWebhook(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.update(WHATSAPP_APP_SECRET=SECRET, WHATSAPP_VERIFY_TOKEN=VERIFY, WHATSAPP_SEND_MODE="dry_run")
        os.environ.pop("WHATSAPP_ALLOW_UNSIGNED", None)
        cls.client = TestClient(app)

    def setUp(self):
        self.t = RecordingTransport()
        service.set_transport(self.t)
        self.wa = "9198" + uuid.uuid4().hex[:8].translate(str.maketrans("abcdef", "123456"))

    def tearDown(self):
        service.set_transport(None)

    def post(self, body, secret=SECRET):
        raw = json.dumps(body).encode()
        sig = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        return self.client.post("/webhooks/whatsapp", content=raw,
                                headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig})

    def say(self, text):
        n = len(self.t.sent)
        r = self.post(envelope(self.wa, text_msg(self.wa, text)))
        self.assertEqual(r.status_code, 200, r.text)
        return self.t.sent[n:]

    def session(self):
        return store.get_or_create_conversation(self.wa)["session_id"]

    # ── handshake & security ──
    def test_verification_handshake(self):
        ok = self.client.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": VERIFY,
                                                           "hub.challenge": "12345"})
        self.assertEqual((ok.status_code, ok.text), (200, "12345"))
        bad = self.client.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "nope",
                                                            "hub.challenge": "1"})
        self.assertEqual(bad.status_code, 403)

    def test_bad_signature_rejected_and_not_processed(self):
        r = self.post(envelope(self.wa, text_msg(self.wa, "I need a wheel barrow")), secret="wrong")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.t.sent, [])

    def test_missing_secret_refuses_unless_dev_flag(self):
        os.environ.pop("WHATSAPP_APP_SECRET")
        try:
            r = self.client.post("/webhooks/whatsapp", json=envelope(self.wa, text_msg(self.wa, "hi")))
            self.assertEqual(r.status_code, 401)
        finally:
            os.environ["WHATSAPP_APP_SECRET"] = SECRET

    # ── conversation ──
    def test_text_gets_reply_with_list_choices(self):
        out = self.say("I need a brush cutter trolley")
        self.assertEqual(out[0][0], self.wa)
        last = out[-1][1]
        self.assertEqual(last["type"], "interactive")
        self.assertEqual(last["interactive"]["type"], "list")          # labels are longer than 20 chars
        rows = last["interactive"]["action"]["sections"][0]["rows"]
        self.assertTrue(all(len(r["title"]) <= 24 for r in rows))
        self.assertTrue(rows[0]["id"].startswith("qr:"))

    def test_list_reply_continues_conversation(self):
        out = self.say("I need a brush cutter trolley")
        row = out[-1][1]["interactive"]["action"]["sections"][0]["rows"][0]
        n = len(self.t.sent)
        self.post(envelope(self.wa, button_msg(self.wa, row["id"], row["title"], "list_reply")))
        reply = self.t.sent[n:][-1][1]
        body = reply.get("interactive", {}).get("body", {}).get("text") or reply.get("text", {}).get("body")
        self.assertIn("sidepack", body.lower())

    def test_recommendation_sends_catalogue_photo(self):
        self.say("I need a wheel barrow")
        out = self.say("it should tip easily")
        img = [m for _, m in out if m["type"] == "image"]
        self.assertEqual(len(img), 1)
        self.assertIn("WB-DUMPER.jpg", img[0]["image"]["link"])
        self.assertIn("₹8,500", img[0]["image"]["caption"])

    def test_duplicate_delivery_processed_once(self):
        msg = text_msg(self.wa, "I need a wheel barrow", mid="wamid.dup." + uuid.uuid4().hex[:6])
        self.post(envelope(self.wa, msg))
        n = len(self.t.sent)
        r = self.post(envelope(self.wa, msg))
        self.assertEqual(r.json()["new_events"], 0)
        self.assertEqual(len(self.t.sent), n)
        self.assertEqual(manager.state(self.session())["turn"], 1)

    def test_burst_processed_in_whatsapp_timestamp_order(self):
        now = int(time.time())
        first = {**text_msg(self.wa, "I need a wheel barrow with easy tipping"), "timestamp": str(now)}
        second = {**text_msg(self.wa, "I will take 2"), "timestamp": str(now + 3)}
        body = envelope(self.wa)
        body["entry"][0]["changes"][0]["value"]["messages"] = [second, first]      # delivered out of order
        self.post(body)
        users = [m["content"] for m in db.get_messages(self.session()) if m["role"] == "user"]
        self.assertEqual(users, [first["text"]["body"], second["text"]["body"]])
        self.assertEqual(manager.state(self.session())["order_preview"]["quantity"], 2)

    def test_confirm_button_is_bound_to_its_preview(self):
        self.say("I need a wheel barrow")
        self.say("it should tip easily")
        out = self.say("I'll take it")
        buttons = out[-1][1]["interactive"]["action"]["buttons"]
        confirm_v1 = buttons[0]["reply"]["id"]
        self.assertTrue(confirm_v1.startswith("confirm:prev_"))
        self.say("make it 2 pieces")                                  # preview v2 supersedes v1
        n = len(self.t.sent)
        self.post(envelope(self.wa, button_msg(self.wa, confirm_v1, "Confirm order")))
        self.assertIsNone(db.get_mock_order(self.session()))            # stale button cannot order v2
        stale = self.t.sent[n:][-1][1]
        self.assertIn("no longer current", json.dumps(stale))
        v2 = manager.state(self.session())["order_preview"]["preview_id"]
        self.post(envelope(self.wa, button_msg(self.wa, f"confirm:{v2}", "Confirm order")))
        order = db.get_mock_order(self.session())
        self.assertEqual((order["order_type"], order["quantity"]), ("MOCK", 2))

    def test_human_takeover_suppresses_bot_and_delivers_agent(self):
        self.say("I want to talk to a salesperson")
        sid = self.session()
        self.client.post(f"/api/v1/sessions/{sid}/handoff", json={"agent": "ravi"})
        before = len(store.outbox_for_session(sid))
        out = self.say("hello?")
        self.assertEqual(out, [])                                     # nothing sent by the bot
        self.assertEqual(len(store.outbox_for_session(sid)), before)  # nothing even queued
        self.assertIn("hello?", [m["content"] for m in db.get_messages(sid)])   # still recorded for the agent
        r = self.client.post(f"/api/v1/sessions/{sid}/agent-message", json={"agent": "ravi", "text": "Hi, Ravi from SGB here"})
        self.assertEqual(r.json()["channel"], "whatsapp")
        self.assertEqual(self.t.sent[-1][1]["text"]["body"], "Hi, Ravi from SGB here")

    def test_outside_24h_window_is_suppressed(self):
        self.say("I need a wheel barrow")
        sid = self.session()
        old = "2020-01-01T00:00:00+00:00"
        store.touch_inbound(self.wa, old)
        n = len(self.t.sent)
        res = service.dispatch(sid, self.wa, [{"type": "text", "text": {"body": "follow-up"}}], source="bot")
        self.assertEqual(res[0]["status"], "SUPPRESSED_OUTSIDE_WINDOW")
        self.assertEqual(len(self.t.sent), n)

    def test_delivery_status_recorded(self):
        self.say("I need a wheel barrow")
        sid = self.session()
        pid = [m for m in store.outbox_for_session(sid) if m["provider_message_id"]][-1]["provider_message_id"]
        self.post(envelope(self.wa, statuses=[{"id": pid, "status": "read", "recipient_id": self.wa}]))
        self.assertEqual([m for m in store.outbox_for_session(sid) if m["provider_message_id"] == pid][0]["delivery_status"], "read")

    def test_unsupported_message_type(self):
        n = len(self.t.sent)
        self.post(envelope(self.wa, {"from": self.wa, "id": "wamid.img." + uuid.uuid4().hex[:6],
                                     "timestamp": str(int(time.time())), "type": "image", "image": {"id": "m1"}}))
        self.assertIn("text messages", self.t.sent[n][1]["text"]["body"])

    def test_numbers_are_masked_in_cockpit(self):
        self.say("hi")
        convs = self.client.get("/api/v1/whatsapp/conversations").json()["conversations"]
        self.assertFalse(any(self.wa in json.dumps(c) for c in convs))
        self.assertTrue(any(c["customer"].endswith(self.wa[-4:]) for c in convs))


class TestGraphTransport(unittest.TestCase):
    """Live transport against a mocked Graph API (no network)."""

    def _transport(self, handler, public_base=None):
        import httpx
        from app.channels.whatsapp.config import WhatsAppConfig
        from app.channels.whatsapp.transport import GraphTransport
        cfg = WhatsAppConfig(enabled=True, send_mode="live", verify_token="v", app_secret="s", access_token="TOKEN",
                             phone_number_id="PNID", api_version="v25.0", graph_base="https://graph.test",
                             public_base_url=public_base, allow_unsigned=False)
        return GraphTransport(cfg, httpx.Client(transport=httpx.MockTransport(handler)))

    def test_send_shape_and_auth(self):
        seen = {}

        def handler(req):
            seen.update(url=str(req.url), auth=req.headers["authorization"], body=json.loads(req.content))
            return __import__("httpx").Response(200, json={"messages": [{"id": "wamid.X"}]})
        res = self._transport(handler).send("919800000000", {"type": "text", "text": {"body": "hi"}, "_x": 1})
        self.assertEqual(res["status"], "SENT")
        self.assertEqual(seen["url"], "https://graph.test/v25.0/PNID/messages")
        self.assertEqual(seen["auth"], "Bearer TOKEN")
        self.assertEqual(seen["body"]["messaging_product"], "whatsapp")
        self.assertNotIn("_x", seen["body"])

    def test_retries_5xx_not_4xx(self):
        import httpx
        calls = {"n": 0}

        def flaky(req):
            calls["n"] += 1
            return httpx.Response(503) if calls["n"] == 1 else httpx.Response(200, json={"messages": [{"id": "w"}]})
        self.assertEqual(self._transport(flaky).send("1", {"type": "text", "text": {"body": "x"}})["status"], "SENT")
        self.assertEqual(calls["n"], 2)
        calls["n"] = 0

        def bad(req):
            calls["n"] += 1
            return httpx.Response(400, json={"error": {"message": "outside window"}})
        res = self._transport(bad).send("1", {"type": "text", "text": {"body": "x"}})
        self.assertEqual((res["status"], calls["n"]), ("FAILED", 1))

    def test_image_uploaded_once_without_public_url(self):
        import httpx
        calls = []

        def handler(req):
            calls.append(req.url.path)
            if req.url.path.endswith("/media"):
                return httpx.Response(200, json={"id": "MEDIA1"})
            return httpx.Response(200, json={"messages": [{"id": "w"}]})
        t = self._transport(handler)
        sha = "testsha-" + uuid.uuid4().hex
        msg = {"type": "image", "image": {"link": "/assets/products/WB-DUMPER.jpg", "caption": "c"},
               "_asset": {"url": "/assets/products/WB-DUMPER.jpg", "sha256": sha}}
        self.assertEqual(t.resolve_media(json.loads(json.dumps(msg)))["image"], {"id": "MEDIA1", "caption": "c"})
        t.resolve_media(json.loads(json.dumps(msg)))
        self.assertEqual(sum(p.endswith("/media") for p in calls), 1)    # cached by image hash

    def test_public_url_links_image(self):
        t = self._transport(lambda r: None, public_base="https://shop.example.in")
        m = t.resolve_media({"type": "image", "image": {"link": "/assets/products/WB-NORMAL.jpg"},
                             "_asset": {"url": "/assets/products/WB-NORMAL.jpg"}})
        self.assertEqual(m["image"]["link"], "https://shop.example.in/assets/products/WB-NORMAL.jpg")


class TestFormatter(unittest.TestCase):
    def test_markup(self):
        t = formatter.to_whatsapp_markup("**Bold** and *italic* and ~~old~~ → new `code`")
        self.assertEqual(t, "*Bold* and _italic_ and ~old~ → new code")

    def test_table_becomes_lines(self):
        md = "| Feature | **A** | **B** |\n|---|---|---|\n| Price | ₹1 | ₹2 |"
        self.assertEqual(formatter.to_whatsapp_markup(md), "*A vs B*\n• Price: ₹1 | ₹2")

    def test_demo_notes_removed(self):
        t = formatter.to_whatsapp_markup("Passed to sales. _(Demo: use the cockpit's Handoff tab.)_")
        self.assertEqual(t, "Passed to sales.")

    def test_buttons_when_short_list_when_long(self):
        b = formatter.build_messages({"reply": "Pick", "quick_replies": ["Sidepack", "Backpack"]})
        self.assertEqual(b[-1]["interactive"]["type"], "button")
        l = formatter.build_messages({"reply": "Pick", "quick_replies": ["I already have a brush cutter", "I want a complete machine"]})
        self.assertEqual(l[-1]["interactive"]["type"], "list")

    def test_long_reply_split_before_buttons(self):
        msgs = formatter.build_messages({"reply": "x" * 1500, "quick_replies": ["Yes"]})
        self.assertEqual([m["type"] for m in msgs], ["text", "interactive"])

    def test_parse_inbound(self):
        self.assertEqual(formatter.parse_inbound(button_msg("1", "confirm:prev_abc"))["bound_preview_id"], "prev_abc")
        self.assertEqual(formatter.parse_inbound(button_msg("1", "qr:Backpack"))["text"], "Backpack")


if __name__ == "__main__":
    unittest.main()
