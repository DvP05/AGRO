"""Finishing a sale properly: multi-item carts, the customer's name and area, and the final summary.

• Cart: "2 dumper barrows and 1 sidepack trolley" becomes ONE preview with two lines. Only products the
  customer named exactly become lines; a bare "wheel barrow" is never guessed into a variant.
• Contact: after the mock order, the bot asks for a name and town/district (no street address: the
  challenge build never collects real delivery addresses) so the sales team can follow up. The customer
  may skip. Contact details live only on the order record and are never written to the audit log.
• Summary: one clear message saying what was ordered, the catalogue total, what is still unknown, and
  exactly what happens next.
"""

import re

from app.nlu.normalize import normalize

RE_SPLIT = re.compile(r"\s*(?:,|\band\b|\bplus\b|&|\balso\b|\bwith\b|\+)\s*")
RE_SKIP = re.compile(r"^(skip|no|no thanks|nope|later|not now|nahi|na|don'?t want|do not want|prefer not|"
                     r"rather not|not needed|no need)[\s.!]*$")
NOT_NAMES = {"interested", "looking", "fine", "ok", "okay", "good", "not", "here", "sure", "ready", "done",
             "buying", "farmer", "a", "the", "from", "in", "yes", "thanks", "thank", "happy", "confirm", "order",
             "what", "how", "when", "where", "is", "it", "next"}
RE_ORDER_STATUS = re.compile(r"\b(what happens next|what next|what now|next steps?|when (will|do|can) i (get|receive)|"
                             r"order status|status of (my )?order|where is my order|my order|track|delivery date|"
                             r"when (is|will) (it|the order) (be )?deliver|did (it|my order) go through|order summary|"
                             r"is my order (placed|confirmed|done))\b")


# ── Cart ─────────────────────────────────────────────────────

def parse_cart(text: str, alias_index: dict[str, str]) -> list[tuple[str, int]]:
    """[(product_id, quantity)] when the message names two or more exact products; otherwise []."""
    t = normalize(text)
    items: dict[str, int] = {}
    for seg in RE_SPLIT.split(t):
        if not seg:
            continue
        hits = sorted((a for a in alias_index if re.search(rf"\b{re.escape(a)}", seg)), key=len, reverse=True)
        if not hits:
            continue
        pid = alias_index[hits[0]]
        q = re.search(r"\b(\d{1,3})\b(?!\s*(?:hp|cc|hours?|months?|years?|l|ltr|litres?|kgs?|acres?|%))", seg)
        qty = int(q.group(1)) if q else 1
        if 0 < qty <= 500:
            items[pid] = items.get(pid, 0) + qty
    return list(items.items()) if len(items) >= 2 else []


def update_cart_item(text: str, items: list[dict], alias_index: dict[str, str]) -> list[dict] | None:
    """'make the dumper 3' / 'remove the trolley' on an open cart preview. None when nothing matched."""
    t = normalize(text)
    hits = sorted((a for a in alias_index if re.search(rf"\b{re.escape(a)}", t)), key=len, reverse=True)
    if not hits:
        return None
    pid = alias_index[hits[0]]
    out = [dict(i) for i in items]
    target = next((i for i in out if i["product_id"] == pid), None)
    if re.search(r"\b(remove|drop|delete|without|no)\b", t):
        if target:
            out.remove(target)
            return out
        return None
    q = re.search(r"\b(\d{1,3})\b", t)
    if not q:
        return None
    if target:
        target["quantity"] = int(q.group(1))
    else:
        out.append({"product_id": pid, "quantity": int(q.group(1))})
    return out


# ── Contact capture ──────────────────────────────────────────

def parse_contact(text: str) -> dict | str | None:
    """'SKIP', {'name', 'area'} (either may be None), or None when the message isn't contact details."""
    raw = text.strip()
    low = raw.lower()
    if RE_SKIP.match(low):
        return "SKIP"
    if "?" in raw or len(raw.split()) > 14:
        return None
    name = area = None
    m = re.search(r"\b(?:my name is|name is|name:|naam|i am|i'm|im|this is|it'?s)\s+([a-z][a-z.' ]{1,30}?)"
                  r"(?=\s*(?:,|\.|\bfrom\b|\bin\b|\bat\b|\band\b|\bdistrict\b|\bvillage\b|$))", low)
    if m:
        name = m.group(1).strip()
    m = re.search(r"\b(?:from|in|at|district|village|town|city|place|location|area|taluk|tehsil)[:\s]+"
                  r"([a-z][a-z.' -]{1,40})", low)
    if m:
        area = re.sub(r"\b(district|village|town|city|taluk|tehsil)\b", "", m.group(1)).strip(" .,-")
    if not name and not area:
        parts = [p.strip() for p in re.split(r"[,\n/]|\s-\s", low) if p.strip()]
        if 1 <= len(parts) <= 3 and all(re.fullmatch(r"[a-z][a-z.' ]{1,40}", p) for p in parts):
            name = parts[0]
            area = parts[1] if len(parts) > 1 else None
    if area:
        area = re.sub(r"\s*\b(district|dist|village|town|city|taluk|tehsil)\b\.?\s*", " ", area).strip(" .,-") or None
    if name and not area and re.search(r"\b(district|dist|village|town|city|taluk|tehsil)\b", name):
        name, area = None, re.sub(r"\s*\b(district|dist|village|town|city|taluk|tehsil)\b\.?\s*", " ", name).strip()
    if name and (set(name.split()) & NOT_NAMES or len(name.split()) > 4):
        name = None
    if area and set(area.split()) & {"stock", "price", "order", "delivery", "it", "that"}:
        area = None
    if not name and not area:
        return None
    return {"name": name.title() if name else None, "area": area.title() if area else None}


def ask_contact(channel: str) -> str:
    where = "on this WhatsApp number" if channel == "whatsapp" else "here"
    return ("\n\nOne last step so the SGB sales team can finalise it: could you share your **name** and your "
            "**town or district**? (No full address needed; the team confirms delivery details with you "
            f"{where}.) Or reply **skip**.")


# ── Summary ──────────────────────────────────────────────────

def order_lines(order: dict) -> list[str]:
    items = (order.get("details") or {}).get("items")
    if items:
        return [f"• {i['quantity']} × {i['name']}: ₹{i['line_total']:,}" for i in items]
    total = f"₹{order['quoted_price']:,.0f}" if order.get("quoted_price") is not None else "price to be confirmed"
    return [f"• {order['quantity']} × {order['product_name']}: {total}"]


def summary(order: dict, contact: dict | None, channel: str, opening: str = "") -> str:
    who = f", {contact['name']}" if contact and contact.get("name") else ""
    step1 = ("The SGB sales team contacts you on this WhatsApp number." if channel == "whatsapp" else
             "The SGB sales team contacts you to take it forward.")
    lines = [opening or f"✅ All set{who}! Here's your order summary:", "",
             f"🧾 **Order {order['order_id']}** (mock order, for the demo)"]
    lines += order_lines(order)
    if order.get("quoted_price") is not None:
        lines.append(f"• **Total (catalogue-listed): ₹{order['quoted_price']:,.0f}**")
    if contact and contact.get("area"):
        lines.append(f"• 📍 Delivery area: {contact['area']} (exact address and date confirmed by the team)")
    lines += ["• Still to confirm: GST, delivery charges and date, stock, payment method", "",
              "**What happens next**",
              f"1. {step1}",
              "2. They confirm stock, GST, delivery charges and date, and how you'd like to pay.",
              "3. Nothing is charged until you agree with them.", "",
              "Anything else I can help with? 🙏"]
    return "\n".join(lines)
