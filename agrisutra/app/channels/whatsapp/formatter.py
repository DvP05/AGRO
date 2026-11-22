"""Turn a dialogue payload into WhatsApp Cloud API message bodies.

The dialogue core writes web-flavoured Markdown. WhatsApp uses its own markup (*bold*, _italic_,
~strike~), has no tables, and limits interactive messages (≤3 reply buttons of ≤20 chars, or a list
of ≤10 rows of ≤24 chars, body ≤1024 chars). This module handles all of that so the sales logic stays
channel-neutral.

Keys starting with "_" are internal metadata (e.g. the local image asset) and are stripped by the sender.
"""

import re

MAX_TEXT = 4096
MAX_INTERACTIVE_BODY = 1024
MAX_BUTTON_TITLE = 20
MAX_ROW_TITLE = 24
MAX_ROW_DESC = 72
MAX_CAPTION = 1024
MAX_IMAGES = 3

DEMO_NOTE = re.compile(r"\s*_\((Demo|Simulator):[^)]*\)_|^_Demo simulator:.*_$", re.MULTILINE)


def to_whatsapp_markup(md: str) -> str:
    text = DEMO_NOTE.sub("", md or "")
    lines, out, rows = text.split("\n"), [], []

    def flush():
        if not rows:
            return
        cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
        body = [c for c in cells[1:] if not all(re.fullmatch(r"[\s:-]*", x) for x in c)]
        head = cells[0]
        out.append("**" + " vs ".join(h.strip("*") for h in head[1:]) + "**")   # → *bold* below
        for c in body:
            vals = " | ".join(c[1:])
            out.append(f"• {c[0]}: {vals}")
        rows.clear()

    for line in lines:
        if line.strip().startswith("|") and line.strip().endswith("|"):
            rows.append(line)
        else:
            flush()
            out.append(line)
    flush()
    t = "\n".join(out)
    t = t.replace("**", "\x00")                                  # protect bold markers
    t = re.sub(r"(?<![\w*\\])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])", r"_\1_", t)   # *italic* → _italic_
    t = t.replace("\x00", "*")
    t = re.sub(r"~~(.+?)~~", r"~\1~", t)
    t = t.replace("\\*", "*")
    t = re.sub(r"`([^`]+)`", r"\1", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _clip(s: str, n: int) -> str:
    s = s.strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def split_text(text: str, limit: int = MAX_TEXT) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for para in text.split("\n\n"):
        while len(para) > limit:                     # pathological single paragraph
            parts.append(para[:limit])
            para = para[limit:]
        if len(cur) + len(para) + 2 > limit:
            parts.append(cur.strip())
            cur = ""
        cur += para + "\n\n"
    if cur.strip():
        parts.append(cur.strip())
    return parts


def reply_id(choice: str) -> str:
    return "qr:" + choice[:200]


def _interactive(body: str, choices: list[tuple[str, str]], header: str | None = None) -> dict:
    """choices: [(id, full label)]. Buttons when they fit, otherwise a list."""
    body = _clip(body, MAX_INTERACTIVE_BODY)
    if len(choices) <= 3 and all(len(label) <= MAX_BUTTON_TITLE for _, label in choices):
        msg = {"type": "interactive", "interactive": {
            "type": "button", "body": {"text": body},
            "action": {"buttons": [{"type": "reply", "reply": {"id": cid, "title": label}} for cid, label in choices]}}}
    else:
        rows = []
        for cid, label in choices[:10]:
            row = {"id": cid, "title": _clip(label, MAX_ROW_TITLE)}
            if len(label) > MAX_ROW_TITLE:
                row["description"] = _clip(label, MAX_ROW_DESC)
            rows.append(row)
        msg = {"type": "interactive", "interactive": {
            "type": "list", "body": {"text": body},
            "action": {"button": "Choose", "sections": [{"title": "Options", "rows": rows}]}}}
    if header:
        msg["interactive"]["header"] = {"type": "text", "text": _clip(header, 60)}
    return msg


def _image(img: dict, caption: str) -> dict:
    return {"type": "image", "image": {"link": img["url"], "caption": _clip(caption, MAX_CAPTION)},
            "_asset": {"url": img["url"], "sha256": img.get("sha256")}}


def build_messages(payload: dict) -> list[dict]:
    """Ordered list of WhatsApp message bodies (without `to` / `messaging_product`)."""
    msgs: list[dict] = []

    # 1. Product photos from the official catalogue, captioned with the catalogue-listed price.
    images = []
    for card in payload.get("product_cards") or []:
        if card.get("image"):
            price = f"₹{card['price']:,}" if card.get("price") is not None else "price not listed"
            images.append(_image(card["image"], f"{card['name']} — {price} (catalogue-listed, p.{card['image']['page']})"))
    if not images:
        for img in payload.get("images") or []:
            images.append(_image(img, img.get("caption", "")))
    preview = payload.get("order_preview")
    if not images and preview and payload.get("next_action") == "AWAIT_EXPLICIT_CONFIRMATION" and preview.get("image"):
        images.append(_image(preview["image"], preview["product_name"]))
    msgs.extend(images[:MAX_IMAGES])

    # 2. The reply text, with buttons / list when the dialogue offered choices.
    text = to_whatsapp_markup(payload.get("reply") or "")
    if not text:
        return msgs
    choices = [(reply_id(q), q) for q in payload.get("quick_replies") or []]
    if preview and payload.get("next_action") == "AWAIT_EXPLICIT_CONFIRMATION":
        # The confirm button is bound to this exact preview version.
        choices = [(f"confirm:{preview['preview_id']}", "Confirm order")] + \
                  [(cid, lbl) for cid, lbl in choices if lbl != "Confirm order"]
        choices = choices[:3]

    if not choices:
        msgs.extend({"type": "text", "text": {"body": part, "preview_url": False}} for part in split_text(text))
    elif len(text) <= MAX_INTERACTIVE_BODY:
        msgs.append(_interactive(text, choices))
    else:
        msgs.extend({"type": "text", "text": {"body": part, "preview_url": False}} for part in split_text(text))
        msgs.append(_interactive("Choose an option, or just type your reply:", choices))
    return msgs


def parse_inbound(msg: dict) -> dict:
    """Normalise one inbound Cloud API message into {text, bound_preview_id, kind}."""
    kind = msg.get("type")
    if kind == "text":
        return {"kind": "text", "text": msg["text"].get("body", "").strip()}
    if kind == "interactive":
        it = msg["interactive"]
        reply = it.get("button_reply") or it.get("list_reply") or {}
        rid = reply.get("id", "")
        if rid.startswith("confirm:"):
            return {"kind": "button", "text": "Confirm order", "bound_preview_id": rid.split(":", 1)[1]}
        if rid.startswith("qr:"):
            return {"kind": "button", "text": rid[3:]}
        return {"kind": "button", "text": reply.get("title", "")}
    if kind == "button":                              # quick-reply button on a template message
        return {"kind": "button", "text": msg["button"].get("payload") or msg["button"].get("text", "")}
    return {"kind": kind or "unknown", "text": ""}
