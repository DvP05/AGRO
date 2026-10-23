"""Safe template-based response composer — no free-form LLM generation."""


def greeting_response() -> str:
    return (
        "👋 Welcome to **AgriSutra** — your evidence-backed agricultural product assistant!\n\n"
        "I can help you find the right sprayer from our catalogue based on your specific needs. "
        "Tell me about your requirements — like your farm size, intended use, or budget — "
        "and I'll recommend products backed by real catalogue data.\n\n"
        "How can I help you today?"
    )


def ask_question_response(question: str, known_fields: dict) -> str:
    context = ""
    if known_fields:
        items = [f"**{k.replace('_', ' ').title()}:** {v}" for k, v in known_fields.items()]
        context = "\n\nHere's what I know so far:\n" + "\n".join(f"• {i}" for i in items) + "\n"
    return f"{question}{context}"


def recommendation_response(ranked: list[dict], eliminated_count: int) -> str:
    if not ranked:
        return (
            "Based on your requirements, I couldn't find any matching products in the catalogue. "
            "Would you like to adjust your requirements, or shall I connect you with our team?"
        )

    lines = [f"Based on your requirements, I found **{len(ranked)} matching product(s)**"]
    if eliminated_count > 0:
        lines[0] += f" (filtered out {eliminated_count} that didn't meet your needs)"
    lines[0] += ":\n"

    for i, p in enumerate(ranked[:3], 1):
        price = p.get("price", {})
        price_str = f"₹{price['amount']}" if price.get("amount") else "Price not listed"
        score_pct = int(p.get("fit_score", 0) * 100)

        lines.append(f"### {i}. {p['name']}")
        lines.append(f"**Category:** {p.get('category', 'N/A')} | **Price:** {price_str} | **Fit:** {score_pct}%")

        # Key specs
        specs = p.get("specifications", {})
        tank = specs.get("tank_capacity", {})
        if isinstance(tank, dict) and tank.get("value"):
            lines.append(f"• Tank: {tank['value']}{tank.get('unit', 'L')}")
        warranty = specs.get("warranty", {})
        if isinstance(warranty, dict) and warranty.get("value"):
            lines.append(f"• Warranty: {warranty['value']} {warranty.get('unit', '')}")

        # Match details
        details = p.get("match_details", [])
        if details:
            lines.append("**Why this matches:**")
            for d in details[:3]:
                lines.append(f"  ✅ {d}")

        # Limitations
        lims = p.get("limitations", [])
        if lims:
            lines.append("**Known limitations:**")
            for l in lims[:2]:
                lines.append(f"  ⚠️ {l}")

        lines.append("")

    lines.append("Would you like more details on any of these, a comparison, or to place an order?")
    return "\n".join(lines)


def correction_response(changes: list[dict], known_fields: dict) -> str:
    lines = ["Thanks for the update! I've adjusted your requirements:\n"]
    for ch in changes:
        old = ch.get("old_value")
        new = ch.get("new_value")
        field_name = ch["field"].replace("_", " ").title()
        if old is not None:
            lines.append(f"• **{field_name}:** ~~{old}~~ → **{new}**")
        else:
            lines.append(f"• **{field_name}:** **{new}**")
    lines.append("\nI'll recalculate the product matches with these updated requirements.")
    return "\n".join(lines)


def product_fact_response(product: dict, query: str) -> str:
    lines = [f"Here are the details for **{product['name']}**:\n"]

    price = product.get("price", {})
    if price.get("amount") is not None:
        lines.append(f"**Price:** ₹{price['amount']} {price.get('unit', '')}")
    else:
        lines.append("**Price:** Not listed in catalogue")

    lines.append(f"**Category:** {product.get('category', 'N/A')}")
    lines.append(f"**Availability:** {product.get('availability', 'Unknown')}")

    specs = product.get("specifications", {})
    if specs:
        lines.append("\n**Specifications:**")
        for k, v in specs.items():
            label = k.replace("_", " ").title()
            if isinstance(v, dict):
                val = f"{v.get('value', '?')} {v.get('unit', '')}".strip()
            elif isinstance(v, bool):
                val = "Yes" if v else "No"
            elif isinstance(v, list):
                val = ", ".join(str(x) for x in v)
            else:
                val = str(v)
            lines.append(f"• {label}: {val}")

    apps = product.get("applications", [])
    if apps:
        lines.append("\n**Applications:** " + ", ".join(apps))

    lims = product.get("limitations", [])
    if lims:
        lines.append("\n**Known Limitations:**")
        for l in lims:
            lines.append(f"• ⚠️ {l}")

    return "\n".join(lines)


def comparison_response(products: list[dict]) -> str:
    if len(products) < 2:
        return "I need at least two products to compare. Could you specify which products you'd like to compare?"

    lines = ["Here's a side-by-side comparison:\n"]
    lines.append("| Feature | " + " | ".join(p["name"].split()[-2:] + [p["name"].split()[0]] for p in products[:3]) + " |")
    lines.append("|---|" + "---|" * min(len(products), 3))

    # Price row
    prices = []
    for p in products[:3]:
        amt = p.get("price", {}).get("amount")
        prices.append(f"₹{amt}" if amt else "N/A")
    lines.append("| **Price** | " + " | ".join(prices) + " |")

    # Category row
    cats = [p.get("category", "N/A") for p in products[:3]]
    lines.append("| **Type** | " + " | ".join(cats) + " |")

    # Tank capacity
    tanks = []
    for p in products[:3]:
        tc = p.get("specifications", {}).get("tank_capacity", {})
        tanks.append(f"{tc.get('value', '?')}L" if isinstance(tc, dict) else "N/A")
    lines.append("| **Tank** | " + " | ".join(tanks) + " |")

    # Warranty
    warranties = []
    for p in products[:3]:
        w = p.get("specifications", {}).get("warranty", {})
        warranties.append(f"{w.get('value', '?')} {w.get('unit', '')}" if isinstance(w, dict) else "N/A")
    lines.append("| **Warranty** | " + " | ".join(warranties) + " |")

    lines.append("\nWould you like me to go deeper into any specific feature or help you decide?")
    return "\n".join(lines)


def order_preview_response(product: dict, quantity: int) -> str:
    price = product.get("price", {})
    unit_price = price.get("amount", 0)
    total = unit_price * quantity if unit_price else None

    lines = [
        "📋 **Mock Order Preview**\n",
        f"**Product:** {product['name']}",
        f"**Product ID:** {product['product_id']}",
        f"**Quantity:** {quantity}",
        f"**Unit Price:** {'₹' + str(unit_price) if unit_price else 'Not listed'}",
        f"**Total:** {'₹' + str(total) if total else 'Cannot compute — price missing'}",
        f"**Availability:** {product.get('availability', 'Unknown')}",
        "",
        "⚠️ **This is a simulated order. No payment will be taken.**",
        "",
        "Please type **\"Confirm\"** to create this mock order, or let me know if you'd like to change anything.",
    ]
    return "\n".join(lines)


def order_confirmed_response(order_id: str, product_name: str, quantity: int) -> str:
    return (
        f"✅ **Mock Order Created Successfully!**\n\n"
        f"**Order ID:** `{order_id}`\n"
        f"**Product:** {product_name}\n"
        f"**Quantity:** {quantity}\n"
        f"**Order Type:** MOCK (Simulated)\n"
        f"**Payment Status:** PENDING / NOT APPLICABLE\n\n"
        f"This is a simulated order for demonstration purposes. "
        f"No actual payment has been processed."
    )


def unknown_fact_response(query: str) -> str:
    return (
        f"I'm unable to verify that information from the supplied catalogue. "
        f"The catalogue does not contain data to answer: *\"{query}\"*\n\n"
        f"I can only provide facts that are backed by our product catalogue. "
        f"Would you like me to flag this for review by our team?"
    )


def escalation_response() -> str:
    return (
        "I understand you'd like to speak with a person. "
        "I've flagged this conversation for **human review**. "
        "In a live system, a team member would be notified to take over. "
        "Is there anything else I can help with in the meantime?"
    )


def cancel_response() -> str:
    return (
        "No problem at all! I've noted that you're not proceeding with an order at this time. "
        "Feel free to come back anytime you need help finding the right product. 👋"
    )


def fallback_response() -> str:
    return (
        "I'm not quite sure what you're looking for. Could you tell me more about:\n"
        "• What type of sprayer you need?\n"
        "• Your farm size or area to cover?\n"
        "• Your approximate budget?\n\n"
        "Or you can ask me about a specific product from our catalogue!"
    )
