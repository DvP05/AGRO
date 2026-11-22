"""Template response composer. It only renders values handed to it by the
decision layer — it never creates a product fact of its own."""

import re

# Unconfirmed source items worth volunteering to a customer; the rest stay in the Truth Receipt.
CUSTOMER_CAUTIONS = {"capacity", "engine_displacement", "push_trolley"}

FAMILY_REPLIES =["Wheel Barrow", "Brush Cutter Trolley", "Brush Cutter machine", "Other enquiry"]


def _cite(claim: dict) -> str:
    return f"p.{claim['source_page']}" if claim.get("source_page") else "catalogue"


def welcome(st: dict | None = None) -> str:
    if st is not None and st.get("said", {}).get("welcome"):
        st["said"]["welcome"] += 1
        return "Hello again! 😊 What can I help you with?"
    if st is not None:
        st.setdefault("said", {})["welcome"] = 1
    return ("Namaste! 🙏 I'm AgriSutra from SGB Agro Industries.\n\n"
            "Tell me what you need help with on your farm or site, in your own words. For example: "
            "_\"I need to carry cement and sand\"_ or _\"grass has grown too much in my field\"_. "
            "I'll suggest the right product and tell you the price.\n\n"
            "We make **wheel barrows**, **brush cutters** and **brush cutter trolleys**.\n\n"
            "_Demo simulator: no real WhatsApp messages, payments or orders._")


def ask(question: str, ack: str = "") -> str:
    return (ack + "\n\n" if ack else "") + question


def change_ack(changes: list[dict], superseded_name: str | None = None) -> str:
    parts = []
    for c in changes:
        if c["type"] == "correction":
            parts.append(f"**{c['label']}**: ~~{_fmt(c['old_value'])}~~ → **{_fmt(c['new_value'])}**")
    if not parts:
        return ""
    msg = "Updated your requirements — " + "; ".join(parts) + "."
    if superseded_name:
        msg += f" My earlier recommendation (**{superseded_name}**) and its Truth Receipt are now superseded."
    return msg


def _fmt(v) -> str:
    if v is True:
        return "yes"
    if v is False:
        return "no"
    if isinstance(v, str):
        return v.replace("_", " ")
    return str(v)


def product_block(ev: dict, claims: list[dict]) -> str:
    stated = [c for c in claims if c["subject_product_id"] == ev["product_id"] and c["evidence_status"] == "SUPPORTED"]
    lines = []
    for c in stated:
        q = f" _({c['qualifier']})_" if c.get("qualifier") and c["claim_type"] != "price" else ""
        lines.append(f"• {c['label']}: {c['rendered_value']}{q} [{_cite(c)}]")
    caution = [c for c in claims if c["subject_product_id"] == ev["product_id"]
               and c["evidence_status"] in ("UNAPPROVED", "CONFLICTED") and c["claim_type"] in CUSTOMER_CAUTIONS]
    for c in caution:
        lines.append(f"• ⚠️ {c['label']}: not confirmed — {c.get('note') or 'needs SGB approval'}")
    return "\n".join(lines)


def recommendation(evals: list[dict], claims: list[dict], excluded: list[dict], ack: str = "") -> str:
    head = ack + "\n\n" if ack else ""
    top = evals[0]
    why_top = pick_reason(top, evals[1] if len(evals) > 1 else None)
    if len(evals) == 1:
        head += f"Based on what you've told me, I'd go with **{top['name']}**" + (f": {why_top}." if why_top else ".")
    else:
        alt = evals[1]
        head += (f"My pick is **{top['short_name']}**" + (f": {why_top}." if why_top else ".")
                 + f" The **{alt['short_name']}** is also a good fit"
                 + (f" if you'd rather save ₹{alt['price'] and top['price'] and top['price'] - alt['price']:,}."
                    if alt.get("price") and top.get("price") and alt["price"] < top["price"] else ".")
                 + "\n\nHere are both, from the catalogue:")
    out = [head]
    for i, ev in enumerate(evals):
        title = f"\n**{i + 1}. {ev['name']}**" if len(evals) > 1 else ""
        why = [c for c in ev["checks"] if c["result"] == "PASS" and c["criterion"] != "product_family"]
        why_txt = ("\n_Why:_ " + "; ".join(f"{c['label'].lower()} ✓" for c in why)) if why else ""
        unv = [c for c in ev["checks"] if c["result"] in ("UNKNOWN", "CONFLICTED")]
        unv_txt = ("\n_Not verified:_ " + "; ".join(c["reason"] for c in unv)) if unv else ""
        pref = ("\n_Preference fit:_ " + "; ".join(ev["preference_details"])) if ev.get("preference_details") else ""
        out.append(f"{title}\n{product_block(ev, claims)}{why_txt}{pref}{unv_txt}")
    if excluded:
        names = ", ".join(f"{e['short_name']} ({e['counterfactual']['failed_criteria'][0]['reason']})"
                          for e in excluded[:2] if e.get("counterfactual", {}).get("failed_criteria"))
        if names:
            out.append(f"\n_Not selected:_ {names}.")
    out.append("\nPrices are catalogue-listed; stock, delivery and GST aren't in the catalogue yet.")
    return "\n".join(out)


def pick_reason(ev: dict, other: dict | None) -> str:
    """Why this product leads, in one human phrase, from its own fit checks and preference details."""
    good = [d for d in ev.get("preference_details", [])
            if not d.startswith(("catalogue does not", "carburetor is", "has a dumper", "running time not"))]
    if other is not None:
        mine = set(good)
        theirs = set(d for d in other.get("preference_details", []))
        good = [d for d in good if d not in theirs] or good
        if not good and ev.get("price") and other.get("price") and ev["price"] < other["price"]:
            return f"it fits what you need and costs less (₹{ev['price']:,} vs ₹{other['price']:,})"
        _ = mine
    if good:
        return _humanise(good[0])
    passed = [c for c in ev.get("checks", []) if c["result"] == "PASS" and c["criterion"] != "product_family"]
    if passed:
        if passed[0]["criterion"] == "budget" and ev.get("price"):
            return f"at ₹{ev['price']:,} it's within your budget"
        return "it meets your " + " and ".join(c["label"].lower() for c in passed[:2])
    return ""


def _humanise(d: str) -> str:
    """Turn a fit-engine preference detail into the way a salesperson would say it."""
    m = re.match(r"running time: (.+?) as provided", d)
    if m:
        return f"it runs about {m.group(1)} per the catalogue, the longest in the range, which suits long working days"
    m = re.match(r"engine warranty: (.+?) \(", d)
    if m:
        return f"it has a {m.group(1)} engine warranty, the longest available"
    m = re.match(r"matches carburetor preference \((.+)\)", d)
    if m:
        return f"it has the {m.group(1)} you prefer"
    m = re.match(r"catalogue describes use for (.+)", d)
    if m:
        return f"the catalogue lists it for {m.group(1)}"
    return d


def no_match(excluded: list[dict]) -> str:
    lines = ["I couldn't find a catalogue product that meets all your requirements:"]
    for e in excluded[:3]:
        cf = e.get("counterfactual") or {}
        reasons = "; ".join(f["reason"] for f in cf.get("failed_criteria", []))
        lines.append(f"• **{e['short_name']}** — {reasons}. Would qualify {cf.get('would_qualify', '')}.")
    lines.append("\nYou can change a requirement, or I can pass this to a salesperson to look for an option.")
    return "\n".join(lines)


def fact_answer(claims: list[dict], multi: bool = False) -> str:
    """Answer a fact question. A qualifier shared by every line (e.g. the price caveat) is said once, not per line."""
    quals = {c.get("qualifier") for c in claims if c["evidence_status"] == "SUPPORTED"}
    shared = next(iter(quals)) if len(quals) == 1 and len(claims) > 1 and None not in quals else None
    if shared:
        claims = [{**c, "qualifier": None} for c in claims]
    subjects = {c["subject_product_id"] for c in claims}
    if len(subjects) == 1 and len(claims) > 1 and all(c["evidence_status"] == "SUPPORTED" for c in claims):
        pages = sorted({c["source_page"] for c in claims if c.get("source_page")})
        body = (f"Here's the **{claims[0]['subject_name']}**:\n"
                + "\n".join(f"• {c['label']}: **{c['rendered_value']}**"
                            + (f" _({c['qualifier']})_" if c.get("qualifier") else "") for c in claims)
                + (f"\n[p.{', '.join(map(str, pages))}]" if pages else ""))
        return body + (f"\n\n_({shared})_" if shared else "")
    body = _fact_lines(claims)
    if multi and all(c["evidence_status"] == "SUPPORTED" for c in claims) and len(claims) > 1:
        body = "Here you go:\n\n" + body
    if shared:
        body += f"\n\n_({shared})_"
    return body


def _fact_lines(claims: list[dict]) -> str:
    # Merge identical answers for several products (e.g. both 143R models share one source conflict).
    groups: list[tuple[list[str], dict]] = []
    for c in claims:
        key = (c["claim_type"], c["evidence_status"], c.get("rendered_value"), c.get("observed_value"), c.get("note"))
        for names, rep in groups:
            if (rep["claim_type"], rep["evidence_status"], rep.get("rendered_value"), rep.get("observed_value"),
                    rep.get("note")) == key and c["evidence_status"] != "SUPPORTED":
                names.append(c["subject_name"])
                break
        else:
            groups.append(([c["subject_name"]], c))
    lines = []
    for names, c in groups:
        s, who = c["evidence_status"], " and ".join(f"**{n}**" for n in names)
        pages = sorted({x["source_page"] for x in claims if x["subject_name"] in names and x["claim_type"] == c["claim_type"] and x["source_page"]})
        where = "p." + ", ".join(str(p) for p in pages) if pages else "catalogue"
        if s == "SUPPORTED":
            q = f" ({c['qualifier']})" if c.get("qualifier") else ""
            note = f" {c['note']}" if c.get("note") and c.get("qualifier") == "as provided" else ""
            lines.append(f"{who} — {c['label']}: **{c['rendered_value']}**{q} [{_cite(c)}].{note}")
        elif s == "UNAPPROVED":
            lines.append(f"{who} — {c['label']}: I can't confirm this. \"{c['observed_value']}\" appears only in the "
                         f"{c['source_location']} on {where}, so SGB has to approve it before I can state it as the "
                         f"{c['label'].lower()}.")
        elif s == "CONFLICTED":
            lines.append(f"{who} — {c['label']}: **not confirmed** — the source conflicts ({where}). {c.get('note')} "
                         "It's flagged for SGB review, so I won't state a figure.")
        else:
            lines.append(f"{who} — {c['label']}: {missing_text(c)}")
    return "\n\n".join(lines)


def missing_text(c: dict) -> str:
    t = c["claim_type"]
    if t == "exact_fitment":
        return ("**UNKNOWN**. The catalogue says the trolley is intended for "
                "sidepack/backpack brush cutters in general, but has no make/model fitment table. "
                "I won't promise a universal fit — a salesperson can confirm it for your exact machine.")
    if t in ("stock", "delivery", "gst", "payment"):
        return "not available in the supplied information. A salesperson can confirm it."
    if t == "warranty":
        return "the catalogue doesn't state warranty terms for this product. \"Not stated\" isn't the same as \"no warranty\" — a salesperson can confirm."
    return "the catalogue doesn't state this, so I can't confirm it."


def _months(v: str) -> int | None:
    v = str(v).lower()
    if v.startswith("no"):
        return 0
    m = re.search(r"(\d+)\s*(year|month)", v)
    if not m:
        return None
    return int(m.group(1)) * (12 if m.group(2) == "year" else 1)


def comparison_take(products: list[dict], rows: list[tuple[str, list[dict]]], fp=None) -> str:
    """A salesperson's one-line steer built only from the compared, supported facts."""
    by_price = sorted(products, key=lambda p: p["price"]["amount"])
    cheap, top = by_price[0], by_price[-1]
    if cheap is top:
        return ""
    row = {label.lower(): cl for label, cl in rows}
    parts = [f"pick the **{cheap['short_name']}** if you want to keep the cost down (₹{cheap['price']['amount']:,})"]
    war = row.get("engine warranty")
    if war:
        months = {c["subject_product_id"]: _months(c["rendered_value"]) for c in war if c["evidence_status"] == "SUPPORTED"}
        best = max((pid for pid in months if months[pid] is not None), key=lambda pid: months[pid], default=None)
        if best and best != cheap["product_id"] and months[best]:
            bp = next(p for p in products if p["product_id"] == best)
            parts.append(f"go for the **{bp['short_name']}** if a longer engine warranty matters "
                         f"({next(c['rendered_value'] for c in war if c['subject_product_id'] == best)})")
    tip = row.get("tipping mechanism")
    if tip and len(parts) == 1:
        dumper = next((c for c in tip if c["evidence_status"] == "SUPPORTED" and "dump" in str(c["rendered_value"]).lower()
                       and c["subject_product_id"] != cheap["product_id"]), None)
        if dumper:
            parts.append(f"choose the **{dumper['subject_name']}** if you unload often, since it tips the load out for you")
    if len(parts) == 1:
        parts.append(f"the **{top['short_name']}** is the step up if those extra features matter to you")
    want = fp.get("warranty_preference") if fp is not None else None
    lead = "**My take:** " + "; ".join(parts) + "."
    if want:
        lead += " Since you want the longest warranty, I'd lean towards the second."
    return lead


def comparison(products: list[dict], rows: list[tuple[str, list[dict]]], take: str = "") -> str:
    head = "| Feature | " + " | ".join(f"**{p['short_name']}**" for p in products) + " |"
    sep = "|---|" + "---|" * len(products)
    lines = ["Side-by-side, from the catalogue:\n", head, sep]
    for label, claims in rows:
        cells = []
        for c in claims:
            if c["evidence_status"] == "SUPPORTED":
                cells.append(str(c["rendered_value"]) + (" *" if c.get("qualifier") == "as provided" else ""))
            elif c["evidence_status"] in ("UNAPPROVED", "CONFLICTED"):
                cells.append("⚠️ unconfirmed")
            else:
                cells.append("not stated")
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    diffs = []
    for label, claims in rows:
        vals = [str(c["rendered_value"]) if c["evidence_status"] == "SUPPORTED" else "not stated" for c in claims]
        if len(set(vals)) > 1:
            diffs.append(f"{label.lower()} ({' vs '.join(vals)})")
    same = [label.lower() for label, claims in rows
            if all(c["evidence_status"] == "SUPPORTED" for c in claims)
            and len({str(c["rendered_value"]) for c in claims}) == 1]
    if diffs and not take:
        lines.append("\n**Key differences:** " + "; ".join(diffs) + ".")
    if same:
        lines.append("**Same on both:** " + ", ".join(same) + "." if len(products) == 2 else
                     "**Same across all:** " + ", ".join(same) + ".")
    if take:
        lines.append("\n" + take + " Tell me what matters most to you and I'll confirm the best fit.")
    elif diffs:
        lines.append("Whether the higher-priced one is worth it depends on how much those differences matter to "
                     "you — tell me what's most important and I'll recommend one.")
    if any(c.get("qualifier") == "as provided" for _, cl in rows for c in cl):
        lines.append("\n\\* running time marked \"as provided\" in the catalogue; not a guaranteed figure.")
    return "\n".join(lines)


def discount(product: dict | None, decision: dict) -> str:
    if not product:
        return ("I don't have authority to offer discounts. Tell me which product you're interested in and I'll "
                "share the catalogue-listed price; a salesperson handles any special pricing.")
    return (f"The catalogue-listed price for **{product['short_name']}** is **₹{product['price']['amount']:,}**.\n\n"
            f"I can't offer or promise a discount: DealGuard returned **{decision['outcome']}** — "
            f"{decision['reason']}\n\nI've passed your discount request to the SGB sales team with your requirements, "
            "so you won't need to repeat anything.")


def objection(product: dict | None, value_claims: list[dict], cheaper: list[dict]) -> str:
    if not product:
        return "I understand. What budget do you have in mind? I'll only suggest products that fit it."
    lines = [f"I understand. Here's what the catalogue lists for **{product['short_name']}** at "
             f"₹{product['price']['amount']:,}:"]
    for c in value_claims:
        lines.append(f"• {c['label']}: {c['rendered_value']} [{_cite(c)}]")
    if cheaper:
        alt = ", ".join(f"**{p['short_name']}** (₹{p['price']['amount']:,})" for p in cheaper)
        lines.append(f"\nA lower-priced option in the same range: {alt}. Want me to compare them?")
    lines.append("\nI can't change the price myself — tell me your budget, or ask for a salesperson.")
    return "\n".join(lines)


def order_preview(p: dict, limitations: list[str]) -> str:
    total = f"₹{p['line_total']:,}" if p["line_total"] is not None else "not available"
    lines = [f"🧾 **Order preview (v{p['version']}) — MOCK / SIMULATION**"]
    if p.get("items"):
        lines += [f"• {i['quantity']} × **{i['name']}** @ ₹{i['unit_price']:,} = ₹{i['line_total']:,}" for i in p["items"]]
        lines.append(f"• **Total (catalogue-listed): {total}**")
    else:
        lines += [f"• Product: **{p['product_name']}**",
                  f"• Quantity: {p['quantity']}",
                  f"• Catalogue-listed price: ₹{p['unit_price']:,} each → **{total}**"]
    lines += [
             "• Not yet known: " + ", ".join(p["terms_unknown"])]
    for lim in limitations:
        lines.append(f"• ⚠️ {lim}")
    lines.append("\nNo payment is taken and no real order is placed. Reply **Confirm order** to create the mock order, "
                 "or tell me what to change.")
    return "\n".join(lines)


def order_confirmed(order: dict) -> str:
    total = f"₹{order['quoted_price']:,.0f}" if order.get("quoted_price") is not None else "N/A"
    items = (order.get("details") or {}).get("items")
    what = ("\n".join(f"• {i['quantity']} × {i['name']}: ₹{i['line_total']:,}" for i in items) + f"\n• Total: {total} (catalogue-listed)"
            if items else f"• {order['product_name']} × {order['quantity']} — {total} (catalogue-listed)")
    return (f"✅ **Mock order created** — `{order['order_id']}`\n"
            f"{what}\n"
            f"• Order type: **MOCK** · Payment: **NOT APPLICABLE**\n\n"
            "This is a simulation; no payment or delivery is arranged. In production a salesperson would confirm "
            "GST, delivery and payment.")


def duplicate_order(order: dict) -> str:
    return (f"You already have mock order `{order['order_id']}` in this conversation, so I haven't created another. "
            "Repeated confirmations never create duplicate orders.")


def nothing_to_confirm() -> str:
    return "There's no order preview to confirm yet. Tell me which product you'd like and I'll prepare one."


def agreement_needs_confirm(p: dict) -> str:
    return (f"Glad it looks right! To protect you, I only create an order on an explicit confirmation. "
            f"Reply **Confirm order** to place the mock order for {p['quantity']} × **{p['product_name']}**.")


def handoff(reason: str) -> str:
    why = {"ASK_FOR_HUMAN": "as you asked", "CUSTOMER_SUPPORT": "for support",
           "DISCOUNT_REQUEST": "for the pricing request", "UNKNOWN_FACT": "to confirm the missing detail",
           "NO_SAFE_MATCH": "to find a suitable option"}.get(reason, "")
    return (f"I've passed this conversation to the SGB sales team {why}. They'll see the full chat, your "
            "requirements and the products we discussed, so you won't need to repeat anything. "
            "_(Demo: use the cockpit's Handoff tab to take over as the agent.)_")


def cancelled(had_preview: bool) -> str:
    return ("Done — I've cancelled the order preview. Nothing was ordered." if had_preview else
            "No problem — I've closed this enquiry. Message any time if you need SGB products.")


def opted_out() -> str:
    return ("You're opted out — no follow-up or promotional messages will be sent. "
            "If you message us again, we'll still reply to you.")


def follow_up(recorded: bool) -> str:
    if not recorded:
        return "You've opted out of messages, so I can't schedule a follow-up. You can message us any time."
    return ("Sure — I've noted a follow-up request. _(Simulator: no message will actually be sent; in production "
            "the scheduler re-checks your consent and WhatsApp rules before sending.)_")


def fallback(st: dict | None = None) -> str:
    opts = [
        "Sorry, I didn't quite catch that. 🙏 Could you tell me what work you need help with? For example "
        "_carrying soil_ or _cutting grass_. You can also ask *what do you sell?*",
        "I'm not sure I understood, my apologies. Try telling me the job in a few words, or say *talk to sales "
        "team* and a person will help you.",
    ]
    if st is None:
        return opts[0]
    said = st.setdefault("said", {})
    n = said.get("fallback", 0)
    said["fallback"] = n + 1
    return opts[n % len(opts)]


def other_enquiry() -> str:  # noqa: D401
    return ("The current catalogue covers wheel barrows, brush cutter trolleys and brush cutters. For anything "
            "else I can pass you to the sales team — or tell me what you need and I'll check the catalogue.")


def out_of_catalogue() -> str:
    return ("That isn't in SGB's current catalogue, which covers wheel barrows, brush cutter trolleys and brush "
            "cutters. I won't guess about other products — I can connect you with the sales team if you like.")
