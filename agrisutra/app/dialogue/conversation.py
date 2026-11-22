"""Conversation layer: the everyday messages a good salesperson handles without thinking.

"How are you?", "what do you sell?", "which is cheapest?", "is it any good?", "the first one",
"thanks", "do you deliver?", "I need something for my farm" — none of these name a product slot,
so the slot-filling engine alone would answer every one of them with the same menu question.

This module recognises those *conversational acts* with high-precision rules (no LLM), and answers
them the way a person would: acknowledge what was said, answer what can be answered from the
catalogue, be honest about what isn't known, and move the conversation forward with one easy
question. Every product fact it mentions comes from the catalogue store or the evidence gate.
"""

import re

from app.nlu.normalize import normalize
from app.dialogue import answers
from app.sales import checkout
from app.sales.responses import CUSTOMER_CAUTIONS

CAUTIONS = CUSTOMER_CAUTIONS | {"engine_type", "rust_resistance"}


def _dedupe(claims: list[dict]) -> list[dict]:
    seen, out = set(), []
    for c in claims:
        if c["claim_type"] not in seen:
            seen.add(c["claim_type"])
            out.append(c)
    return out

# ── Act patterns (run on normalised text) ────────────────────

RE_THANKS = re.compile(r"\b(thanks?|thank (you|u)|thx|ty|dhanyavaa?d|shukriya|much appreciated|great help)\b")
RE_BYE = re.compile(r"^(ok(ay)? )?(bye+|good ?bye|see (you|u)|tata|good night|talk (to you )?later|cya)\b")
RE_HOW_ARE_YOU = re.compile(r"\b(how are (you|u)|how r u|how do you do|how'?s it going|kaise ho|kya haal|how have you been)\b")
RE_WHO = re.compile(r"\b(who are (you|u)|who is this|what are you|are you (a |an )?(bot|robot|human|real|person|ai|machine)|"
                    r"your name|am i (talking|chatting) (to|with))\b")
RE_CAPABILITIES = re.compile(r"\b(what can (you|u) do|how can (you|u) help|what do (you|u) do|what (all )?can i ask|"
                             r"how does this work)\b|^help( me)?$")
RE_COMPANY = re.compile(r"\b(about (your|the|this) (company|business|firm|shop)|who (is|are) sgb|about sgb|"
                        r"where (are you|is your (shop|company|office)) (located|based)?|your (address|location|shop|office)|"
                        r"where are you\b|company details)\b")
RE_OVERVIEW = re.compile(r"\bwhat (all )?(do|does|can) (you|u|sgb)( guys| people| all)? (sell|have|offer|make|manufacture|supply)|"
                         r"\bwhat (all )?(products|items|things|options|machines)\b|\b(which|what) products\b|"
                         r"\byour (products|range|catalogue|items)\b|\bproduct (list|range|catalogue)\b|\bprice list\b|"
                         r"\b(show|send|tell) me (all|everything|your (products|range|catalogue))\b|\bfull (list|catalogue|range)\b|"
                         r"\ball (your )?(products|prices|items)\b|\bwhat else (do you have|is there)\b|\bwhat all is there\b")
RE_BEST = re.compile(r"\b(best|top|most popular|best ?sell(er|ing)|bestseller|most sold|number one|no\.? ?1)\b"
                     r"(?! (price|rate|deal|discount|offer))")
RE_CHEAPEST = re.compile(r"\b(cheapest|lowest (price|cost|priced)|least expensive|most affordable|low(est)? budget|"
                         r"budget (option|one|model)|cheap(er)? (one|option|model)|something cheap|less expensive|"
                         r"economical|affordable)\b")
RE_PRICIEST = re.compile(r"\b(most expensive|costliest|highest price|premium (one|model|option)|top (end|model)|"
                         r"most (advanced|powerful))\b")
RE_HOW_TO_BUY = re.compile(r"\b(how (do|can|to) (i |we )?(buy|order|purchase|pay|book)|how to (buy|order|purchase|pay)|"
                           r"where (can|do) i (buy|get|order)|payment (options|methods|mode)|can i pay|"
                           r"cash on delivery|\bcod\b|\bupi\b|\bemi\b|online payment)")
RE_DELIVERY = re.compile(r"\b(do (you|u) deliver|deliver(y)? (to|in|at)|ship(ping)? (to|in)|courier|home delivery|"
                         r"delivery (charges?|time|available)|how (many|long) days|when will (it|i get)|dispatch)\b")
RE_STOCK = re.compile(r"\b(in stock|available now|availability|is it available|do you have (it|stock)|stock available)\b")
RE_SERVICE = re.compile(r"\b(service (centre|center)|spare parts?|spares|repair|servicing|after ?sales|mechanic)\b")
RE_VAGUE = re.compile(r"\b(farm(ing)?|field|fields|khet|kheti|garden|land|acres?|crops?|plantation|orchard|"
                      r"sugarcane|paddy|rice|wheat|cotton|coconut|banana|mango|areca|grapes?|tea estate|estate|"
                      r"agricultur\w*|nursery)\b|"
                      r"\b(something|anything|kuch)\b|\b(don'?t|do not|dont) know what\b|\bnot sure (what|which)\b|"
                      r"\b(need|want) (some )?help\b|\bconfused\b|\bwhat should i (buy|get|use)\b|"
                      r"\b(recommend|suggest) (me )?(something|anything|a product|one)\b|\bguide me\b")
RE_OUT_OF_SCOPE = re.compile(r"\b(spray(er|ing)?|pesticides?|insecticides?|plough(ing)?|plow(ing)?|till(er|ing)|"
                             r"irrigat\w*|drip|water(ing)? (my|the)|dig(ging)?|sow(ing)?|seeders?|"
                             r"thresh\w*|harvest(er|ing machine)|tractors?|pumps?|generators?|chainsaws?|"
                             r"chain saws?|drones?|rotavators?|power ?weeders?|cultivators?)\b")
RE_ORDINAL = re.compile(r"\b(first|1st|second|2nd|third|3rd|last|top|cheaper|cheapest|costlier|expensive) "
                        r"(one|option|product|model|item|machine)\b|\b(option|number|no\.?) ?([1-7])\b|^([1-7])$|"
                        r"\bthe (first|second|third|last)\b")
RE_OPINION = re.compile(r"\b(is (it|this|that) (good|worth|reliable|durable|any good|ok|okay|nice|value for money)|"
                        r"(are|r) (they|these) (good|worth|reliable)|should i (buy|get|take|go for) (it|this|that|one)|"
                        r"worth (it|buying|the (money|price))|your (opinion|suggestion|advice|recommendation)|"
                        r"what do (you|u) (think|suggest|recommend)|honest(ly)?( opinion)?|would (you|u) (recommend|buy)|"
                        r"which (one )?(should|would) (i|you) (buy|get|take|choose|pick)|good (quality|product))\b")
RE_REPLACEMENT = re.compile(r"\b(broke|broken|not working|stopped working|dead|damaged|worn out|got stolen|stolen|"
                            r"is gone|need (a )?(new|replacement)|replace (it|my|the))\b")
RE_TELL_MORE = re.compile(r"^(tell me more|more (details|info|information)|details( please)?|full details|"
                          r"tell me (about|more about) (it|this|that)|what about (it|this)|explain( it)?|specs?|specifications)\b")
RE_ACK = re.compile(r"^(ok(ay)?|k|sure|fine|alright|right|hmm+|good|great|nice|cool|achha|accha|theek hai|yes|yeah|yup|ya|no|nope)[\s.!]*$")
RE_PRAISE = re.compile(r"\b(good bot|you are (good|great|helpful|smart)|very helpful|nice service|good service|awesome|superb)\b")
RE_COMPLAINT = re.compile(r"\b(useless|stupid|not helpful|you don'?t understand|not understanding|wrong answer|"
                          r"same (question|thing|answer) again|you('re| are) a robot|irritating|waste of time)\b")

HARD_INTENTS = {"OPT_OUT", "CONFIRM_ORDER", "ASK_FOR_HUMAN", "REQUEST_DISCOUNT", "RAISE_PRICE_OBJECTION",
                "CORRECT_REQUIREMENT", "EDIT_ORDER", "REQUEST_FOLLOW_UP"}

FAMILY_LABEL = {"wheel_barrow": "Wheel barrows", "brush_cutter_trolley_attachment": "Brush cutter trolleys",
                "brush_cutter_machine": "Brush cutters"}
FAMILY_PURPOSE = {  # taken from the catalogue's own product descriptions
    "wheel_barrow": "for moving soil, manure, farm produce and construction material",
    "brush_cutter_trolley_attachment": "puts the brush cutter you already own on wheels, so you push it instead of carrying it",
    "brush_cutter_machine": "for cutting grass and weeds across your farm",
}
FAMILY_ORDER = ["wheel_barrow", "brush_cutter_machine", "brush_cutter_trolley_attachment"]

JOB_REPLIES = ["Move soil / loads", "Cut grass & weeds", "I own a brush cutter"]


def detect(text: str, intent: str, entities: dict, st: dict) -> str | None:
    """Return a conversational act, or None to let the sales engine handle the turn."""
    t = normalize(text).strip(" ?!.")
    # A rule-backed hard intent always wins; a weak model-only guess ("kya haal hai" → discount?) does not.
    if intent in HARD_INTENTS and (st.get("nlu_rule") or intent in ("CONFIRM_ORDER", "OPT_OUT")):
        return None
    fam = entities.get("product_family") or entities.get("family_hint") == "trolley_ambiguous"
    has_need = bool(fam or entities.get("mentioned_product") or entities.get("trolley_configuration")
                    or entities.get("need_type") or entities.get("owns_brush_cutter"))
    words = len(t.split())
    if st.get("order") and checkout.RE_ORDER_STATUS.search(t):
        return "ORDER_STATUS"
    qa = answers.detect(text, entities, st)          # open catalogue questions come first
    if qa:
        return qa

    if RE_THANKS.search(t) and words <= 6 and not has_need and "?" not in text:
        return "THANKS"
    if RE_BYE.search(t) and words <= 6:
        return "GOODBYE"
    if RE_COMPLAINT.search(t):
        return "COMPLAINT"
    if RE_PRAISE.search(t) and not has_need:
        return "PRAISE"
    if RE_HOW_ARE_YOU.search(t) and not has_need:
        return "HOW_ARE_YOU"
    if RE_WHO.search(t):
        return "WHO_ARE_YOU"
    if RE_CAPABILITIES.search(t) and not has_need:
        return "CAPABILITIES"
    if RE_COMPANY.search(t):
        return "ABOUT_COMPANY"
    if RE_HOW_TO_BUY.search(t) and not st.get("order_preview"):
        return "HOW_TO_BUY"
    if RE_DELIVERY.search(t) and not entities.get("mentioned_product") and not st["fingerprint"].get("product_family"):
        return "DELIVERY"
    if RE_SERVICE.search(t):
        return "SERVICE"
    if RE_STOCK.search(t) and not has_need and not st.get("selected_product_id") and not st["fingerprint"].get("product_family"):
        return "STOCK"
    if st.get("last_list") and RE_ORDINAL.search(t) and not entities.get("mentioned_product"):
        return "ORDINAL"
    if RE_TELL_MORE.search(t) and (st.get("fact_focus_id") or st.get("selected_product_id")) \
            and not entities.get("fact_topics"):
        return "TELL_MORE"
    if RE_OPINION.search(t) and (not entities.get("product_family") or len(entities.get("mentioned_products", [])) == 1):
        return "OPINION"
    no_family = not entities.get("product_family") and not st["fingerprint"].get("product_family")
    if RE_REPLACEMENT.search(t) and no_family and not entities.get("mentioned_product") \
            and not entities.get("trolley_configuration") and not re.search(r"\b(cutter|barrow|trolley)\b", t):
        return "REPLACEMENT"            # "my old machine broke" — ask what it was, don't assume a trolley
    if RE_CHEAPEST.search(t) and no_family and RE_VAGUE.search(t) and not has_need:
        st["budget_conscious"] = True
        return "VAGUE_NEED"             # "something cheap for my garden" — find the job first, then the price
    if RE_CHEAPEST.search(t) and not entities.get("budget"):
        return "CHEAPEST"
    if RE_PRICIEST.search(t):
        return "PRICIEST"
    if RE_OVERVIEW.search(t) and not entities.get("product_family"):
        return "OVERVIEW"
    if RE_BEST.search(t) and not entities.get("product_family") and not st["fingerprint"].get("product_family"):
        return "BEST"
    if RE_OUT_OF_SCOPE.search(t) and not has_need:
        return "OUT_OF_SCOPE"
    if RE_REPLACEMENT.search(t) and not has_need and not st["fingerprint"].get("product_family"):
        return "REPLACEMENT"
    if not has_need and not st["fingerprint"].get("product_family") and RE_VAGUE.search(t):
        return "VAGUE_NEED"
    if RE_ACK.match(t) and intent in ("AGREEMENT", "UNKNOWN", "GREETING") and not st.get("last_question") \
            and not st.get("order_preview") and not st.get("selected_product_id"):
        return "IDLE_ACK"
    return None


# ── Helpers ──────────────────────────────────────────────────

def vary(st: dict, key: str, options: list[str]) -> str:
    """Never say the exact same sentence twice in a row: rotate through phrasings per conversation."""
    said = st.setdefault("said", {})
    n = said.get(key, 0)
    said[key] = n + 1
    return options[n % len(options)]


def rupees(n: int) -> str:
    return f"₹{n:,}"


def price_range(products: list[dict]) -> str:
    prices = sorted(p["price"]["amount"] for p in products)
    return rupees(prices[0]) if prices[0] == prices[-1] else f"{rupees(prices[0])}–{rupees(prices[-1])}"


def family_question(st: dict) -> str:
    """The opening discovery question, asked like a person would — about the job, not the SKU."""
    return vary(st, "family_q", [
        "Tell me a little about the work you need help with — for example **carrying soil or manure**, or "
        "**cutting grass and weeds**? I'll point you to the right product.",
        "No problem. What's the main job — moving loads around (soil, cement, harvest), or clearing grass and "
        "weeds? If you already own a brush cutter and just want a trolley for it, tell me that.",
        "Just so I suggest the right thing: are you looking to **carry** things, or **cut** grass/weeds?",
    ])


def overview_text(store) -> str:
    lines = ["Here's everything in SGB's current catalogue:"]
    for fam in FAMILY_ORDER:
        prods = store.get_family(fam)
        if not prods:
            continue
        lines.append(f"\n**{FAMILY_LABEL[fam]}** ({price_range(prods)}) — {FAMILY_PURPOSE[fam]}")
        for p in sorted(prods, key=lambda x: x["price"]["amount"]):
            lines.append(f"• {p['short_name']} — {rupees(p['price']['amount'])}")
    return "\n".join(lines)


def overview_ids(store) -> list[str]:
    ids = []
    for fam in FAMILY_ORDER:
        ids += [p["product_id"] for p in sorted(store.get_family(fam), key=lambda x: x["price"]["amount"])]
    return ids


# ── Act handlers ─────────────────────────────────────────────

def respond(m, sid: str, st: dict, act: str, text: str, e: dict, r: dict) -> bool:
    """Fill `r` for the act. Returns False when the act turned out to need the sales engine after all."""
    if act == "ORDER_STATUS":
        o = st["order"]
        contact = (o.get("details") or {}).get("contact") or st.get("contact")
        r.update(reply=checkout.summary(o, contact, st.get("channel", "browser"),
                                        opening="Your order is placed. Here's where it stands:"),
                 next_action="ORDER_STATUS", quick_replies=["Talk to sales team"])
        r["conversation_act"] = act
        return True
    if act in ("CAPABILITY", "SUPERLATIVE", "EASE_OF_USE"):
        return answers.respond(m, sid, st, act, text, e, r)
    store = m.store
    fam = st["fingerprint"].get("product_family")
    sel = store.get_product(st["selected_product_id"]) if st.get("selected_product_id") else None
    focus = sel or (store.get_product(st["fact_focus_id"]) if st.get("fact_focus_id") else None)

    def done(reply: str, action: str, qr: list[str] | None = None):
        r.update(reply=reply, next_action=action, quick_replies=qr or [])
        r["conversation_act"] = act
        return True

    if act == "THANKS":
        if st.get("order"):
            return done("You're welcome! 🙏 Your mock order is noted. The SGB team will confirm delivery and "
                        "payment details. Anything else I can help with?", "SMALL_TALK")
        lq = st.get("last_question") or {}
        if lq.get("question") and not sel:
            return done("You're welcome! 😊 Whenever you're ready, just one thing so I can suggest the right model: "
                        + lq["question"], "SMALL_TALK", lq.get("quick_replies") or [])
        if sel:
            return done(vary(st, "thanks", [
                f"You're welcome! 😊 Take your time. When you're ready, I can make an order preview for "
                f"**{sel['short_name']}**, or answer any other question.",
                "Happy to help! Anything else you'd like to know?"]), "SMALL_TALK",
                ["Show order preview", "Compare options"])
        return done("You're welcome! 😊 If you need anything for your farm later — wheel barrows or brush "
                    "cutters — just message me.", "SMALL_TALK")

    if act == "GOODBYE":
        if st.get("order_preview") and st["order_preview"]["status"] == "PENDING_CONFIRMATION":
            return done("Bye for now! 🙏 Your order preview is saved here, nothing is ordered until you reply "
                        "**Confirm order**.", "SMALL_TALK")
        return done("Thank you for messaging SGB Agro Industries! 🙏 Have a good day. Message me any time.",
                    "SMALL_TALK")

    if act == "HOW_ARE_YOU":
        return done("I'm doing well, thank you for asking! 😊 I hope all is good on your side.\n\n"
                    + ("Shall we continue with the " + sel["short_name"] + "?" if sel else
                       "How can I help you today — are you looking for something to **carry** loads, or to "
                       "**cut** grass and weeds?"),
                    "SMALL_TALK", [] if sel else JOB_REPLIES)

    if act == "WHO_ARE_YOU":
        return done("I'm **AgriSutra**, SGB Agro Industries' assistant on WhatsApp. 🌱 I'm an automated assistant, "
                    "not a person. I can help you pick the right wheel barrow or brush cutter, tell you prices and "
                    "specs from SGB's catalogue, and prepare an order. Whenever you'd like a real person, just say "
                    "**talk to sales team**.", "SMALL_TALK", JOB_REPLIES)

    if act == "CAPABILITIES":
        return done("Here's how I can help:\n"
                    "• Suggest the right product for your work, just describe the job\n"
                    "• Share prices, warranty and specs straight from SGB's catalogue\n"
                    "• Compare models side by side\n"
                    "• Prepare an order preview for you to confirm\n"
                    "• Connect you to the SGB sales team any time\n\n"
                    "What would you like to start with?", "SMALL_TALK",
                    ["See all products", "Cut grass & weeds", "Move soil / loads"])

    if act == "ABOUT_COMPANY":
        return done("SGB Agro Industries makes farm equipment: wheel barrows, brush cutters and brush cutter "
                    "trolleys. 🚜 My information comes from their product catalogue, which doesn't include the "
                    "company address or branch details, so I won't guess. The sales team can share those.\n\n"
                    "Would you like to see the product range, or talk to the team?", "SMALL_TALK",
                    ["See all products", "Talk to sales team"])

    if act == "HOW_TO_BUY":
        step = (f"I can prepare an order preview for **{sel['short_name']}** right now." if sel else
                "First, let's find the right product. Tell me what you need it for.")
        return done("Buying is simple:\n"
                    "1. Pick the product (I'll help you choose)\n"
                    "2. I prepare an order preview with the price and quantity\n"
                    "3. You reply **Confirm order**\n"
                    "4. The SGB sales team contacts you to confirm payment, GST and delivery\n\n"
                    "I don't take any payment in this chat, and payment options aren't in the catalogue, so the team "
                    "will confirm those with you. " + step, "SMALL_TALK",
                    ["Show order preview", "Talk to sales team"] if sel else JOB_REPLIES)

    if act == "DELIVERY":
        return done("Good question. Delivery areas, charges and timelines aren't in the catalogue I work from, so I "
                    "can't promise them. The SGB sales team confirms delivery when they call about your order. "
                    + ("Shall I prepare an order preview for *" + sel["short_name"] + "* so they have everything?"
                       if sel else "Meanwhile, I can help you choose the right product. What do you need it for?"),
                    "SMALL_TALK", ["Show order preview", "Talk to sales team"] if sel else
                    ["Talk to sales team"] + JOB_REPLIES[:2])

    if act == "SERVICE":
        return done("Spare parts and service details aren't in SGB's catalogue, so I'd rather not guess. "
                    "The sales team can tell you about service support near you. Want me to connect you?",
                    "SMALL_TALK", ["Talk to sales team"])

    if act == "STOCK":
        return done("Live stock isn't in the catalogue I use, so I can't confirm availability. The sales team "
                    "checks that before confirming any order. Which product are you interested in? I'll share the "
                    "details meanwhile.", "SMALL_TALK", JOB_REPLIES)

    if act == "ORDINAL":
        pid = _resolve_ordinal(normalize(text), st, store)
        if not pid:
            return False
        p = store.get_product(pid)
        st["fact_focus_id"] = pid
        return _describe(m, sid, st, p, r, act)

    if act == "TELL_MORE":
        return _describe(m, sid, st, store.get_product(st["fact_focus_id"]) if st.get("fact_focus_id") else focus, r, act)

    if act == "OPINION":
        p = store.get_product(e["mentioned_product"]) if e.get("mentioned_product") else focus
        if not p:
            done("Happy to give you my honest view. Which product do you have in mind? Or tell me what "
                 "work you'll use it for and I'll tell you which one I'd pick.", "SMALL_TALK", JOB_REPLIES)
            return True
        return _opinion(m, sid, st, p, r, act)

    if act in ("CHEAPEST", "PRICIEST"):
        pool = store.get_family(fam) if fam else store.get_all_products()
        rec = st.get("last_recommendation")
        if fam and rec:
            viable = [pid for pid in rec["eligible"] + rec["unverified"]]
            pool = [p for p in pool if p["product_id"] in viable] or pool
        pool = sorted(pool, key=lambda p: p["price"]["amount"])
        p = pool[0] if act == "CHEAPEST" else pool[-1]
        st["fact_focus_id"] = p["product_id"]
        if fam:
            st["selected_product_id"] = p["product_id"]     # "the cheaper one" is a choice, not just a question
        st["last_list"] = [x["product_id"] for x in pool]
        claim = m.evidence_gate.resolve(p["product_id"], "price")
        m._set_receipt(st, [claim], sid)
        scope = f"among {FAMILY_LABEL[fam].lower()}" if fam else "in the whole catalogue"
        others = ", ".join(f"{x['short_name']} ({rupees(x['price']['amount'])})" for x in pool if x is not p)
        word = "lowest-priced" if act == "CHEAPEST" else "highest-priced"
        extra = ""
        if not fam and act == "CHEAPEST":
            extra = (" Note that it's a trolley **attachment**, so it's only useful if you already own a brush "
                     "cutter." if p["family"] == "brush_cutter_trolley_attachment" else "")
        reply = (f"The {word} option {scope} is **{p['short_name']}** at **{rupees(p['price']['amount'])}** "
                 f"(catalogue-listed price).{extra}\n\nFor comparison: {others}.\n\n"
                 + ("The cheapest isn't always the best fit, so tell me what you'll use it for and I'll "
                    "check it suits you." if not fam else "Would you like the full details or an order preview?"))
        r["images"] = [i for i in [_img(p)] if i]
        return done(reply, "ANSWER_WITH_EVIDENCE", ["Tell me more"] + (["Show order preview"] if fam else JOB_REPLIES[:2]))

    if act == "OVERVIEW":
        st["last_list"] = overview_ids(store)
        st["last_question"] = {"slot": "product_family"}
        return done(overview_text(store) + "\n\nWhat will you be using it for? Tell me the job and I'll suggest "
                    "the best fit.", "OVERVIEW", JOB_REPLIES)

    if act == "BEST":
        st["last_question"] = {"slot": "product_family"}
        return done("Honestly, the best one depends on the job, because SGB makes very different things:\n"
                    "• To **carry** soil, manure or harvest → a wheel barrow (the **Dumper** tips its load for you)\n"
                    "• To **cut** grass and weeds → a brush cutter (the **143R WALBRO** has the longest engine "
                    "warranty, 1 year)\n"
                    "• Already own a brush cutter → a trolley so you can push it instead of carrying it\n\n"
                    "What will you mainly use it for?", "SMALL_TALK", JOB_REPLIES)

    if act == "OUT_OF_SCOPE":
        st["last_question"] = {"slot": "product_family"}
        thing = _out_of_scope_name(normalize(text))
        bridge = ""
        if re.search(r"spray|pesticide|insecticide", text.lower()):
            bridge = (" If you're clearing weeds before spraying, a brush cutter might help, and a wheel barrow "
                      "is handy for moving fertiliser and pesticide cans around the field.")
        return done(f"Sorry, SGB doesn't make {thing}, and I don't want to point you to the wrong product. "
                    f"SGB's range is wheel barrows, brush cutters and brush cutter trolleys.{bridge}\n\n"
                    "Is any of those useful for you, or shall I connect you with the sales team?",
                    "OUT_OF_CATALOGUE", JOB_REPLIES[:2] + ["Talk to sales team"])

    if act == "REPLACEMENT":
        st["last_question"] = {"slot": "product_family"}
        return done("Sorry to hear that, it's frustrating when a machine lets you down in the middle of work. "
                    "What was it: a brush cutter, or something else? If it was a brush cutter, I can show you "
                    "SGB's models with their warranty.", "ASK_CLARIFYING_QUESTION",
                    ["It was a brush cutter", "It was a wheel barrow", "Something else"])

    if act == "VAGUE_NEED":
        st["last_question"] = {"slot": "product_family"}
        st["lead_status"] = "DISCOVERY" if st["lead_status"] == "NEW" else st["lead_status"]
        crop = re.search(r"\b(sugarcane|paddy|rice|wheat|cotton|coconut|banana|mango|areca|grapes?|tea|orchard|"
                         r"garden|nursery|plantation)( farm| plantation| field| fields| orchard| estate| crop)?\b",
                         text.lower())
        opener = (f"Sure, happy to help with your {crop.group(0)}!" if crop else
                  vary(st, "vague_open", ["Sure, I'll help you find the right thing.",
                                          "No problem, let's work it out together."]))
        def frm(f):
            return rupees(min(p["price"]["amount"] for p in store.get_family(f)))
        budget = " I'll keep your budget in mind." if st.get("budget_conscious") else ""
        return done(opener + budget + " SGB makes two kinds of equipment:\n"
                    f"• **Wheel barrows** {FAMILY_PURPOSE['wheel_barrow']} (from {frm('wheel_barrow')})\n"
                    f"• **Brush cutters** {FAMILY_PURPOSE['brush_cutter_machine']} (from "
                    f"{frm('brush_cutter_machine')}), and trolleys from {frm('brush_cutter_trolley_attachment')} "
                    "if you already own a brush cutter\n\n"
                    "Which job do you need help with?", "ASK_CLARIFYING_QUESTION", JOB_REPLIES)

    if act == "IDLE_ACK":
        return done(vary(st, "idle", [
            "👍 What can I help you with? You can describe the work, for example **I need to carry cement**, or ask "
            "to **see all products**.",
            "Sure. Whenever you're ready, tell me what you need, or ask me anything about SGB's products."]),
            "SMALL_TALK", ["See all products"] + JOB_REPLIES[:2])

    if act == "PRAISE":
        return done("Thank you, that's kind of you! 😊 Anything else I can help with?", "SMALL_TALK")

    if act == "COMPLAINT":
        st["last_question"] = None
        return done("Sorry about that, I'm an automated assistant and I didn't get it right. 🙏 Could you tell me "
                    "in a few words what you need? For example **a wheel barrow for cement** or *price of the "
                    "WALBRO brush cutter*. Or I can connect you with a person from the sales team right away.",
                    "SMALL_TALK", ["Talk to sales team", "See all products"])
    return False


def _img(p: dict) -> dict | None:
    from app.dialogue.manager import _image_view
    return _image_view(p)


def _resolve_ordinal(t: str, st: dict, store) -> str | None:
    ids = st.get("last_list") or []
    if not ids:
        return None
    m = re.search(r"\b(first|1st|second|2nd|third|3rd|last|top|cheaper|cheapest|costlier|expensive)\b", t)
    word = m.group(1) if m else None
    n = re.search(r"\b(?:option|number|no\.?) ?([1-7])\b|^([1-7])$", t)
    if n:
        idx = int(n.group(1) or n.group(2)) - 1
    else:
        idx = {"first": 0, "1st": 0, "top": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2, "last": -1}.get(word)
    if word in ("cheaper", "cheapest"):
        return min(ids, key=lambda pid: store.get_product(pid)["price"]["amount"])
    if word in ("costlier", "expensive"):
        return max(ids, key=lambda pid: store.get_product(pid)["price"]["amount"])
    if idx is None or not (-len(ids) <= idx < len(ids)):
        return None
    return ids[idx]


def _out_of_scope_name(t: str) -> str:
    m = RE_OUT_OF_SCOPE.search(t)
    w = m.group(1) if m else "that"
    names = {"spray": "sprayers", "spraying": "sprayers", "sprayer": "sprayers", "pesticide": "sprayers",
             "pesticides": "sprayers", "insecticide": "sprayers", "insecticides": "sprayers",
             "plough": "ploughs", "ploughing": "ploughing equipment", "plow": "ploughs", "plowing": "ploughing equipment",
             "tiller": "tillers", "tilling": "tillers", "drip": "irrigation equipment", "digging": "digging tools",
             "dig": "digging tools", "sowing": "sowing equipment", "sow": "sowing equipment", "seeder": "seeders",
             "seeders": "seeders"}
    if w.startswith("irrigat") or w.startswith("water"):
        return "irrigation equipment"
    if w.startswith("thresh"):
        return "threshers"
    return names.get(w, w if w.endswith("s") else w + "s")


def _describe(m, sid: str, st: dict, p: dict, r: dict, act: str) -> bool:
    """Friendly product summary from supported catalogue claims only."""
    claims = _dedupe(m.evidence_gate.summary_claims(p["product_id"]))
    m._set_receipt(st, claims, sid)
    stated = [c for c in claims if m.evidence_gate.statable(c)]
    lines = [f"**{p['short_name']}**, here's what SGB's catalogue says:"]
    for c in stated:
        lines.append(f"• {c['label']}: {c['rendered_value']}")
    unconfirmed = [c for c in claims if c["evidence_status"] in ("UNAPPROVED", "CONFLICTED")
                   and c["claim_type"] in CAUTIONS]
    if unconfirmed:
        lines.append("• Not confirmed yet: " + ", ".join(c["label"].lower() for c in unconfirmed)
                     + " (I'll only state these once SGB approves them)")
    if p["family"] == "brush_cutter_trolley_attachment":
        lines.append("\nThis is an attachment, so it's for a brush cutter you already own.")
    lines.append("\nWould you like an order preview, or shall I compare it with the others?")
    st["selected_product_id"] = p["product_id"]
    r["images"] = [i for i in [_img(p)] if i]
    r.update(reply="\n".join(lines), next_action="ANSWER_WITH_EVIDENCE",
             quick_replies=["Show order preview", "Compare options", "Is it worth it?"])
    r["conversation_act"] = act
    return True


def _opinion(m, sid: str, st: dict, p: dict, r: dict, act: str) -> bool:
    """An honest salesperson's view: who it suits, what stands out, what to know — catalogue facts only."""
    gate = m.evidence_gate
    claims = _dedupe(gate.summary_claims(p["product_id"]))
    m._set_receipt(st, claims, sid)
    by = {c["claim_type"]: c for c in claims}
    good = [c for c in claims if gate.statable(c) and c["claim_type"] not in ("price", "product_type", "described_uses")
            and not str(c["rendered_value"]).lower().startswith("no ")]
    rank = ["engine_warranty", "warranty", "tipping_mechanism", "carburetor", "run_time", "construction",
            "operation", "wheel_design", "power", "engine_type"]
    good.sort(key=lambda c: rank.index(c["claim_type"]) if c["claim_type"] in rank else len(rank))
    uses = by.get("described_uses")
    family = m.store.get_family(p["family"])
    out = [f"My honest take on **{p['short_name']}** ({rupees(p['price']['amount'])}):"]
    if uses and gate.statable(uses):
        out.append(f"The catalogue describes it for {uses['rendered_value']}.")
    if good:
        out.append("What stands out:\n" + "\n".join(f"• {c['label']}: {c['rendered_value']}" for c in good[:3]))
    know = []
    w = by.get("warranty") or by.get("engine_warranty")
    if w and gate.statable(w) and str(w["rendered_value"]).lower().startswith("no"):
        know.append("it comes with no engine warranty")
    unconf = [c["label"].lower() for c in claims if c["evidence_status"] in ("UNAPPROVED", "CONFLICTED")
              and c["claim_type"] in CAUTIONS]
    if unconf:
        know.append(f"the {unconf[0]} isn't confirmed by SGB yet" if len(unconf) == 1 else
                    f"these aren't confirmed by SGB yet: {', '.join(unconf)}")
    if p["family"] == "brush_cutter_trolley_attachment":
        know.append("exact fitment for your machine has to be confirmed by the sales team")
    if know:
        out.append("Worth knowing: " + "; ".join(know) + ".")
    alts = sorted((x for x in family if x["product_id"] != p["product_id"]), key=lambda x: x["price"]["amount"])
    if alts and p["family"] != "brush_cutter_trolley_attachment":   # trolleys differ by fit, not by tier
        cheaper = [x for x in alts if x["price"]["amount"] < p["price"]["amount"]]
        dearer = [x for x in alts if x["price"]["amount"] > p["price"]["amount"]]
        if cheaper:
            out.append(f"If budget matters more, the **{cheaper[0]['short_name']}** is {rupees(cheaper[0]['price']['amount'])}.")
        if dearer:
            out.append(f"If you want more, the **{dearer[-1]['short_name']}** ({rupees(dearer[-1]['price']['amount'])}) is the step up.")
    out.append("\nI can only vouch for what's in the catalogue, but for that job it's a sensible choice. "
               "Shall I prepare an order preview?")
    st["selected_product_id"] = p["product_id"]
    r["images"] = [i for i in [_img(p)] if i]
    r.update(reply="\n".join(out), next_action="ANSWER_WITH_EVIDENCE",
             quick_replies=["Show order preview", "Compare options"])
    r["conversation_act"] = act
    return True
