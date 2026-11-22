"""Chat with the WhatsApp bot from your terminal, without Meta: a fake phone that sends signed webhooks.

1. Put the same secret in .env for the server and this script:   WHATSAPP_APP_SECRET=any-long-random-string
2. Start the server:   python run.py
3. Run:                python scripts/whatsapp_simulator.py [--phone 919800000001] [--url http://localhost:8000]

Type a message and press Enter. When the bot offers buttons or a list, type the option number to tap it.
Replies are read back from the server's outbox, exactly as they would be sent to WhatsApp.
"""

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import uuid

import httpx

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def load_secret() -> str | None:
    secret = os.environ.get("WHATSAPP_APP_SECRET")
    env_path = os.path.join(ROOT, ".env")
    if not secret and os.path.exists(env_path):
        for line in open(env_path, encoding="utf-8"):
            if line.strip().startswith("WHATSAPP_APP_SECRET="):
                secret = line.split("=", 1)[1].strip().strip('"')
    return secret


def envelope(phone: str, message: dict, name: str) -> dict:
    return {"object": "whatsapp_business_account", "entry": [{"id": "SIMULATED_WABA", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "metadata": {"display_phone_number": "SIMULATED", "phone_number_id": "SIMULATED"},
        "contacts": [{"wa_id": phone, "profile": {"name": name}}], "messages": [message]}}]}]}


def render(m: dict) -> tuple[str, list[tuple[str, str]]]:
    """Return printable text and tappable options [(id, title)]."""
    p, opts = m["payload"], []
    if p["type"] == "text":
        text = p["text"]["body"]
    elif p["type"] == "image":
        text = f"[🖼  photo: {p['image'].get('link') or p['image'].get('id')}]\n{p['image'].get('caption', '')}"
    elif p["type"] == "interactive":
        it = p["interactive"]
        text = it["body"]["text"]
        if it["type"] == "button":
            opts = [(b["reply"]["id"], b["reply"]["title"]) for b in it["action"]["buttons"]]
        else:
            opts = [(r["id"], r["title"]) for s in it["action"]["sections"] for r in s["rows"]]
    else:
        text = json.dumps(p)
    if m["status"] not in ("SENT", "DRY_RUN"):
        text = f"[{m['status']}: {m.get('error')}]\n" + text
    return text, opts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--phone", default="919800000001", help="fake customer number (digits only)")
    ap.add_argument("--name", default="Test Farmer")
    a = ap.parse_args()
    secret = load_secret()
    if not secret:
        print("Set WHATSAPP_APP_SECRET (env or .env) — the same value the server uses.")
        return 1
    client = httpx.Client(base_url=a.url, timeout=30)
    try:
        status = client.get("/api/v1/whatsapp/status").json()
    except httpx.HTTPError:
        print(f"Server not reachable at {a.url}. Start it with: python run.py")
        return 1
    print(f"📱 Simulated WhatsApp · customer {a.phone} · server mode {status['send_mode']}  (Ctrl+C to quit)\n")
    seen, options, session_id = set(), [], None

    while True:
        try:
            line = input("you › ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        ts = str(int(time.time()))
        mid = f"wamid.SIM.{uuid.uuid4().hex[:16]}"
        if line.isdigit() and options and 1 <= int(line) <= len(options):
            oid, title = options[int(line) - 1]
            kind = "button_reply" if oid.startswith(("confirm:",)) or len(options) <= 3 else "list_reply"
            msg = {"from": a.phone, "id": mid, "timestamp": ts, "type": "interactive",
                   "interactive": {"type": kind, kind: {"id": oid, "title": title}}}
            print(f"      (tapped “{title}”)")
        else:
            msg = {"from": a.phone, "id": mid, "timestamp": ts, "type": "text", "text": {"body": line}}
        raw = json.dumps(envelope(a.phone, msg, a.name)).encode()
        sig = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        r = client.post("/webhooks/whatsapp", content=raw, headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig})
        if r.status_code != 200:
            print(f"  ✗ webhook rejected: HTTP {r.status_code} {r.text}")
            continue
        for _ in range(25):                     # the conversation is created by the background worker
            if session_id:
                break
            convs = client.get("/api/v1/whatsapp/conversations").json()["conversations"]
            mine = [c for c in convs if c["customer"].endswith(a.phone[-4:])]
            session_id = mine[0]["session_id"] if mine else None
            if not session_id:
                time.sleep(0.4)
        if not session_id:
            continue
        # The server answers the webhook first and runs the dialogue in the background: wait for the reply
        # to land in the outbox (and settle), so a later "1" taps the right button.
        new, deadline, last_count = [], time.time() + 15, -1
        while time.time() < deadline:
            new = [m for m in client.get(f"/api/v1/whatsapp/conversations/{session_id}/outbox").json()["messages"]
                   if m["id"] not in seen]
            if new and len(new) == last_count:
                break
            last_count = len(new)
            time.sleep(0.4)
        if not new:
            print("  (no reply — a salesperson may own this chat)")
        for m in new:
            seen.add(m["id"])
            text, opts = render(m)
            print("bot › " + text.replace("\n", "\n      "))
            if opts:
                options = opts
                for i, (_, title) in enumerate(opts, 1):
                    print(f"      [{i}] {title}")
        print()


if __name__ == "__main__":
    sys.exit(main())
