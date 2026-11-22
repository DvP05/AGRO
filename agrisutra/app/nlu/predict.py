"""Intent classification and slot extraction for the SGB domain.

Hybrid by design:
  * text is normalised first (spelling, Hinglish, "15k" → 15000) — see normalize.py;
  * a locally trained TF-IDF + Logistic Regression model predicts the intent;
  * deterministic high-precision rules guard risky classes (order confirmation,
    opt-out, human request, discount) so a model guess can never trigger a side effect;
  * slot rules understand descriptions and use cases ("I carry it on my back",
    "to shift cement bags"), not just product names.
"""

import re
from typing import Any

from app.nlu import model as intent_model
from app.nlu.normalize import normalize

INTENTS = [
    "GREETING", "SELECT_PRODUCT_FAMILY", "DISCOVER_PRODUCT", "ASK_PRODUCT_FACT", "COMPARE_PRODUCTS",
    "STATE_REQUIREMENT", "CORRECT_REQUIREMENT", "RAISE_PRICE_OBJECTION", "REQUEST_DISCOUNT",
    "EXPRESS_BUYING_INTENT", "REQUEST_QUOTE", "CONFIRM_ORDER", "EDIT_ORDER", "CANCEL_OR_STOP",
    "REQUEST_FOLLOW_UP", "ASK_FOR_HUMAN", "CUSTOMER_SUPPORT", "OPT_OUT", "AGREEMENT", "UNKNOWN",
]

# Per-intent abstention thresholds. Risky intents need more confidence.
THRESHOLDS = {"default": 0.22, "CANCEL_OR_STOP": 0.40, "OPT_OUT": 0.45, "EDIT_ORDER": 0.30}

# ── High-precision rules ──────────────────────────────────────
RE_OPT_OUT = re.compile(r"\b(unsubscribe|opt[\s-]?out|stop (sending|messaging|these messages|all messages|messages)|"
                        r"(don'?t|do not) (send|message|contact)|remove my number)\b")
RE_CONFIRM = re.compile(r"^(yes[,!.\s]*)?(please\s+)?(i\s+)?(confirm(ed)?|place)(\s+(the|my|this))?(\s+order)?"
                        r"(\s+(please|now|it))?[\s.!]*$"
                        r"|\b(confirm|place)\s+(the\s+|my\s+|this\s+)?order\b(?!\s*\?)"
                        r"|^(yes[,\s]+)?go ahead and place"
                        r"|^confirmed[,\s]+place it")
RE_CONFIRM_BLOCK = re.compile(r"\?|\b(if|whether|can you|could you|how do i|before i)\b")
RE_HUMAN = re.compile(r"\b(human|real person|salesperson|sales ?person|sales team|representative|"
                      r"agent|someone from sales|speak (to|with) (someone|a person)|talk to (someone|a person))\b")
RE_CANCEL = re.compile(r"\b(cancel|forget it|never ?mind|not interested|drop it|leave it)\b")
RE_DISCOUNT = re.compile(r"\b(discount|best price|best rate|special price|lower the price|reduce the price|"
                         r"any offer|offer running|final price|less price|for less|do (it )?(for|at) \d+|can you do \d+)\b")
# Price objections need a price context: "the grass has grown too much" is not an objection.
RE_OBJECTION = re.compile(r"\b(too (expensive|costly)|very (expensive|costly)|"
                          r"(price|cost|rate)s?( is| seems| looks)? (too|very|quite|a bit) (high|much|steep)|"
                          r"(that|it|this)('s| is) (too|very) (much|high|steep)|out of my budget|"
                          r"not worth (it|the|that)|why so (expensive|costly)|can'?t afford)\b")
RE_CORRECTION = re.compile(r"\b(actually|sorry,? i meant|i meant|instead|change (it |that |budget |quantity )?to|"
                           r"correction|no wait|not \w+ but|on second thought|make it the)\b")
RE_COMPARE = re.compile(r"\b(vs\.?|versus|compared? (to|with)|difference between|differences?|differ|better than|"
                        r"worth (it |the extra( money)? |paying more )?(over|compared)|which (one )?is better|"
                        r"\w+ or the \w+ one)\b")
RE_REQUEST = re.compile(r"\b(suggest|recommend|looking for|i need|i want|need a|need an|want a|want an|show me|"
                        r"something (to|for|that)|which (one|model|product)? ?should i)\b")
# "Which brush cutter do you have with a 1 year warranty?" is a product search, not a fact lookup.
RE_PRODUCT_SEARCH = re.compile(r"\bwhich (\w+ ){0,3}(do you have|have you got|do you sell|comes? with|has|have|gives?)\b|"
                               r"\b(do you have|have you got) (a|an|any) [\w ]{0,25}(with|that)\b")
# Asking for the order preview (our own quick-reply button, and the ways people say it).
RE_PREVIEW = re.compile(r"\b((show|send|give|make|prepare|create)( me)?( the| an| my)? (order )?preview|order preview|"
                        r"(prepare|make|create|place) (the |an |my )?order|i'?ll take (it|this|that|one)|i will take (it|this)|"
                        r"book (it|this|one)|order (it|this|one) now|go ahead( with (it|this|the order))?|buy (it|this) now)\b")
RE_ASKING = re.compile(r"\b(want to know|tell me|is it|does it|will it|can it|is there)\b")
RE_GREETING_ONLY = re.compile(r"^(hi+|hello|hey|namaste|namaskar|good (morning|afternoon|evening))[\s!.,a-z]{0,12}$")


def classify_intent(text: str, entities: dict | None = None) -> tuple[str, float, dict]:
    """Return (intent, confidence, trace). `trace` records model output and rule overrides."""
    t = normalize(text)
    trace: dict[str, Any] = {"model_top": [], "rule": None, "model_version": None, "normalized": t}
    if len(t) < 1:
        return "UNKNOWN", 0.0, trace

    ranked = intent_model.predict_proba(t)
    trace["model_top"] = [{"intent": i, "p": round(p, 3)} for i, p in ranked[:3]]
    trace["model_version"] = intent_model.get_model()["model_version"]
    intent, conf = ranked[0]
    entities = entities or {}

    def rule(name: str, new_intent: str, new_conf: float = 0.95):
        nonlocal intent, conf
        trace["rule"] = name
        intent, conf = new_intent, new_conf

    has_slot = any(k in entities for k in SLOT_KEYS)
    n_products = len(entities.get("mentioned_products", []))
    is_question = "?" in t or bool(re.match(r"(is|does|do|will|what|how|can|could|which|are)\b", t))
    is_request = (bool(RE_REQUEST.search(t)) and not RE_ASKING.search(t)) or bool(RE_PRODUCT_SEARCH.search(t))

    if RE_OPT_OUT.search(t):
        rule("opt_out_pattern", "OPT_OUT")
    elif RE_CONFIRM.search(t) and not RE_CONFIRM_BLOCK.search(t):
        rule("strict_confirmation_pattern", "CONFIRM_ORDER")
    elif RE_HUMAN.search(t) and not re.search(r"\b(are|r) (you|u) (a |an )?(real|human|bot|robot|person|machine)", t):
        rule("human_request_pattern", "ASK_FOR_HUMAN")
    elif RE_DISCOUNT.search(t):
        rule("discount_pattern", "REQUEST_DISCOUNT")
    elif RE_OBJECTION.search(t):
        rule("price_objection_pattern", "RAISE_PRICE_OBJECTION", 0.9)
    elif RE_CANCEL.search(t) and "?" not in t:
        rule("cancel_pattern", "CANCEL_OR_STOP", 0.9)
    elif RE_PREVIEW.search(t) and "?" not in t and not RE_CONFIRM_BLOCK.search(t):
        rule("order_preview_request", "EXPRESS_BUYING_INTENT", 0.9)
    elif RE_COMPARE.search(t) and (n_products >= 2 or "two" in t or "both" in t or entities.get("product_family")
                                   or re.search(r"\b(your|the) 2\b", t)):
        rule("comparison_pattern", "COMPARE_PRODUCTS", 0.9)
    elif RE_CORRECTION.search(t) and has_slot:
        rule("correction_cue_with_slot", "CORRECT_REQUIREMENT", 0.9)
    elif RE_GREETING_ONLY.match(t) and not has_slot:
        rule("greeting_only", "GREETING", 0.95)
    elif entities.get("fact_topics") and is_question and not is_request \
            and intent not in ("COMPARE_PRODUCTS", "REQUEST_DISCOUNT", "CUSTOMER_SUPPORT"):
        rule("fact_question_with_topic", "ASK_PRODUCT_FACT", max(conf, 0.85))
    elif entities.get("fact_topics") and (entities.get("mentioned_product") or entities.get("product_family")) \
            and not is_request and re.match(r"(price|cost|rate|warranty|specs?|specifications|details|power|weight|"
                                             r"running time|tell me|what about|and)\b", t) \
            and intent not in ("COMPARE_PRODUCTS", "REQUEST_DISCOUNT", "CUSTOMER_SUPPORT"):
        rule("topic_led_fact_question", "ASK_PRODUCT_FACT", max(conf, 0.8))
    elif is_request and has_slot and intent in ("ASK_PRODUCT_FACT", "UNKNOWN", "AGREEMENT", "GREETING"):
        rule("request_with_need", "STATE_REQUIREMENT", max(conf, 0.75))
    elif intent == "ASK_PRODUCT_FACT" and not is_question and has_slot:
        # "6 months of warranty is the minimum I'd accept" states a need; it doesn't ask for a fact
        rule("statement_with_need", "STATE_REQUIREMENT", max(conf, 0.75))
    elif intent == "ASK_PRODUCT_FACT" and not entities.get("fact_topics") and has_slot:
        # "what can help me clear the grass?" asks for a product, not a catalogue fact
        rule("need_question_without_fact_topic", "DISCOVER_PRODUCT", max(conf, 0.75))

    # Safety: the model alone can never produce an order confirmation.
    if intent == "CONFIRM_ORDER" and trace["rule"] != "strict_confirmation_pattern":
        trace["rule"] = "confirmation_downgraded_no_strict_match"
        intent, conf = "EXPRESS_BUYING_INTENT", min(conf, 0.6)

    # A stated slot outweighs a weak greeting / agreement guess.
    if intent in ("GREETING", "AGREEMENT", "UNKNOWN") and has_slot and trace["rule"] is None:
        trace["rule"] = "slot_present_overrides_smalltalk"
        intent, conf = "STATE_REQUIREMENT", max(conf, 0.7)

    if trace["rule"] is None and conf < THRESHOLDS.get(intent, THRESHOLDS["default"]):
        trace["rule"] = "abstain_low_confidence"
        intent = "STATE_REQUIREMENT" if has_slot else "UNKNOWN"

    return intent, round(conf, 3), trace


# ── Slot extraction ───────────────────────────────────────────

SLOT_KEYS = ("product_family", "need_type", "trolley_configuration", "dumper_required", "needs_push_trolley",
             "run_time_preference",
             "warranty_min_months", "warranty_preference", "carburetor_preference", "budget", "quantity",
             "machine_model", "capacity_required_litres", "intended_use", "mentioned_product", "declined_slots")

FACT_TOPIC_PATTERNS = [
    ("price", r"\b(price|cost|costs|rate|how much is|how much does|how much for)\b"),
    ("warranty", r"\bwarranty\b"),
    ("engine_displacement", r"\b(\d+\s*cc|cc\b|engine capacity|displacement)"),
    ("power", r"\b(hp|horse ?power|power)\b"),
    ("run_time", r"\b(run(ning)? ?time|how long (does|will|can) (it|the [\w ]{1,30}?) (run|last|work|go)|"
                 r"runs? (for|longest|long)|fuel lasts?|on (one|a|1|full) tank|hours? of (running|work) per|run for how long|"
                 r"how many hours)\b"),
    ("push_trolley", r"\b(does|do|is|will) (it|the [\w ]{1,30}?) (have|has|come|comes|include|includes|ship|shipped|supplied|sold|given|delivered)"
                     r"( with)? (a |the )?(push[\s-]?)?(trolley|wheels?)\b|\b(trolley|wheels?) (included|comes? with it)\b"),
    ("load_capacity", r"\b(load capacity|how much (weight|load)|how many kg|kg capacity|carry how much|"
                      r"how much can it (carry|hold|take)|\d+\s*kgs?\b|carry \d+|weight it can)"),
    ("capacity", r"\b(\d+\s*(l|ltr|litres?|liters?)\b|capacity|volume|how much (soil|sand) (can|does) it hold)"),
    ("construction", r"\b(material|steel|made of|build quality|how strong|sturdy|durable)\b"),
    ("carburetor", r"\bcarburetor\b"),
    ("engine_type", r"\b(2[\s-]?stroke|4[\s-]?stroke|stroke|(what|which|type of|kind of) engine|engine type)\b"),
    ("exact_fitment", r"\b(fit|fits|fitment|compatible|compatibility|suit my|work with my)\b"),
    ("stock", r"\b(in stock|stock|available now|availability|ready to ship)\b"),
    ("delivery", r"\b(deliver(y|ed)?|shipping|courier|dispatch|reach me|how many days)\b"),
    ("dimensions", r"\b(size|dimension|dimensions|length|width|height|how big)\b"),
    ("weight", r"\b(how heavy|weight of|its weight|what is the weight|weigh|heavy is)\b"),
    ("gst", r"\b(gst|tax|inclusive|exclusive)\b"),
    ("payment", r"\b(payment|emi|cash on delivery|cod|upi)\b"),
    ("tipping_mechanism", r"\b(tip|tips|tipping|dump|dumps|dumping)\b"),
    ("described_uses", r"\b(used for|use it for|suitable for|what can i carry|good for)\b"),
    ("fuel_tank", r"\b(fuel tank|tank capacity|petrol tank)\b"),
]

BRANDS = r"(honda|stihl|husqvarna|kisankraft|kisan kraft|balwaan|neptune|oleo[\s-]?mac|makita|shindaiwa|" \
         r"mitsubishi|kawasaki|robin|sgb|maruyama|tata agrico)"

NON_PRICE_UNITS = r"(?:pieces?|pcs|units?|nos?|cc|hp|hours?|hrs?|months?|years?|l|ltr|litres?|liters?|kgs?|acres?|bags?)\b"

# Use-case lexicons → product family (the customer may never name the product).
BARROW_MATERIALS = r"(cement|sand|soil|mud|manure|dung|compost|fertili[sz]er bags?|bricks?|gravel|stones?|debris|" \
                   r"rubble|concrete|fodder|feed bags?|farm produce|vegetables|harvest|coconuts?|bags of \w+|mortar)"
BARROW_ACTIONS = r"(carry|carrying|shift|shifting|move|moving|transport|haul|cart|load|unload|unloading|wheel|take)"
CUTTER_TARGETS = r"(grass|weeds?|bush(es)?|shrubs?|undergrowth|lawn|hedges?|crop residue|stubble|paddy straw|" \
                 r"wild growth|overgrown|jungle|scrub)"
CUTTER_ACTIONS = r"(cut|cutting|clear|clearing|clean|cleaning|trim|trimming|mow|mowing|slash|remove|removing|chop|chopping)"

DECLINE_TOPICS = {"warranty": "warranty_min_months", "carburetor": "carburetor_preference", "budget": "budget",
                  "price": "budget", "trolley": "needs_push_trolley", "tipping": "dumper_required",
                  "dumper": "dumper_required", "brand": None}


def _join(items) -> str:
    items = list(items)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _num(s: str) -> int | None:
    try:
        return int(float(s.replace(",", "")))
    except ValueError:
        return None


def extract_entities(text: str, alias_index: dict[str, str] | None = None) -> dict[str, Any]:
    """Extract typed slot candidates. Values are candidates, not facts, until the dialogue manager applies them."""
    t = " " + normalize(text) + " "
    e: dict[str, Any] = {}

    # Product mention via catalogue aliases (longest alias wins).
    if alias_index:
        hits = sorted((a for a in alias_index if re.search(rf"\b{re.escape(a)}", t)), key=len, reverse=True)
        mentioned = []
        for a in hits:
            pid = alias_index[a]
            if pid not in mentioned:
                mentioned.append(pid)
        if mentioned:
            e["mentioned_products"] = mentioned
            e["mentioned_product"] = mentioned[0]
    # "the stable one", "the normal barrow": bare variant words point at a product when asking about it.
    # Kept separate from mentioned_products so they never rewrite requirements by themselves.
    variants = []
    for word, pid in (("walbro", "143R-WALBRO"), ("stable", "143R-STABLE"), ("varsha", "VARSHA-TROLLEY"),
                      ("normal", "WB-NORMAL"), ("standard", "WB-NORMAL"), ("dumper", "WB-DUMPER"),
                      ("sidepack", "BCT-SIDE"), ("backpack", "BCT-BACK")):
        if re.search(rf"\b{word}\b(?! (carburetor|wheel support|support|frame))", t) and pid not in variants:
            variants.append(pid)
    if variants:
        e["variant_products"] = variants

    # ── Ownership of a brush cutter ──
    owns = bool(re.search(r"\b(already (have|own|got|use)|i (have|own|got|use|bought|purchased)|we (have|own|bought))"
                          r"( got)?( a| an| one| my| the)?( [\w-]+){0,3}? (brush cutter|cutter|machine)\b|"
                          r"\bmy (existing |own |old |current )?([\w-]+ ){0,2}(brush cutter|cutter|machine)\b|"
                          r"\bexisting (brush cutter|machine)\b|\balready (have|own|got) one\b", t))
    no_machine = bool(re.search(r"\b(don'?t|do not|dont) (have|own) (a |any |the )?(brush cutter|machine|cutter|one)\b|"
                                r"\bno (brush cutter|machine) yet\b", t))
    # "my old machine broke, need a new one" — a broken machine is being *replaced*, not fitted with a trolley.
    if re.search(r"\b(broke|broken|not working|stopped working|dead|damaged|worn out|stolen|need (a )?(new|replacement)|"
                 r"replace (it|my|the))\b", t) and not re.search(r"\b(trolley|attachment|wheels?)\b", t):
        no_machine = True
    if owns and not no_machine:
        e["owns_brush_cutter"] = True

    # ── Trolley configuration, by name or by description ──
    if re.search(r"\bsidepack\b|\bon (my|the) side\b|\bside of (my|the) body\b|\bhang(s|ing)? (on|at|by) (my |the )?side|"
                 r"\b(hip|side)[\s-]?(mounted|hanging|hung|slung|carried|type|kind|model)\b|\bshoulder strap\b", t):
        e["trolley_configuration"] = "sidepack"
    elif re.search(r"\bbackpack\b|\bon (my|the) back\b|\bcarr(y|ied) (it )?(on|at) (my )?back\b|"
                   r"\bback[\s-]?(mounted|carried|strapped|type|kind|model)\b|\bstraps? (on|to) my back\b", t):
        e["trolley_configuration"] = "backpack"

    # ── Attachment vs complete machine ──
    wants_wheels = bool(re.search(r"\b(push (it|the machine|my cutter)?( around)?|on wheels|put it on (a )?trolley|"
                                  r"fit it on (a )?trolley|mount it|push[\s-]?type|rather than carry|instead of carrying)\b", t))
    if wants_wheels:
        e["wants_wheels"] = True
    if no_machine or re.search(r"\b(complete|full|whole|new|entire) (machine|brush cutter|set|thing|unit)\b|"
                               r"\b(brush cutter|machine) with (a )?trolley\b|\bvarsha\b", t):
        e["need_type"] = "complete_machine"
    elif e.get("owns_brush_cutter") or re.search(r"\battachment\b|\btrolley only\b|\bjust the trolley\b", t):
        e["need_type"] = "attachment"

    # ── Product family: names first, then use-case inference ──
    wheel = re.search(r"\bwheel barrow\b|\bbarrows?\b|\bdumper\b", t)
    trolley = re.search(r"\btrolley", t)
    cutter = re.search(r"\bbrush cutter\b|\b(grass|weed|bush) cutter\b|\bcutter\b|\btrimmer\b|\bstrimmer\b|"
                       r"\b143 ?r\b|\bwalbro\b|\bgl ?45\b|\bvarsha\b", t)
    # "I have a coconut farm" names a crop, not a load to carry.
    barrow_use = re.search(rf"\b{BARROW_MATERIALS}\b(?! (farm|farms|plantation|trees?|field|estate|orchard|garden|crop))", t) \
        and not cutter and not trolley
    cutter_use = re.search(rf"\b{CUTTER_ACTIONS}\b.*\b{CUTTER_TARGETS}\b|\b{CUTTER_TARGETS}\b.*\b(grown|cut|clear|clean|trim|remove|get rid)", t)
    cutter_use = cutter_use or (re.search(rf"\b(buy|get|use|need|want|recommend|suggest|best)\b.*\bfor\b.*\b{CUTTER_TARGETS}\b", t)
                                and not re.search(rf"\b{BARROW_ACTIONS}\b", t))
    # "my farm has a lot of weeds, what should I use": a grass/weed *problem* is a cutting need too.
    cutter_use = cutter_use or re.search(
        rf"\b(lot of|lots of|full of|too much|so much|growing|grown|covered (in|with)|problem|overgrown|everywhere)\b"
        rf".*\b{CUTTER_TARGETS}\b|\b{CUTTER_TARGETS}\b.*\b(problem|everywhere|growing|out of control|taking over|spreading|too (high|tall|thick)|what (should|can) i use|"
        rf"how (do|can) i (get rid|remove|clear))\b", t)

    if wheel or (barrow_use and not e.get("owns_brush_cutter")):
        e["product_family"] = "wheel_barrow"
        if barrow_use and not wheel:
            e["family_inferred_from"] = "materials to move"
    elif trolley or e.get("trolley_configuration") or (e.get("owns_brush_cutter") and wants_wheels):
        if e.get("need_type") == "complete_machine":
            e["product_family"] = "brush_cutter_machine"
            e["needs_push_trolley"] = True
        elif e.get("need_type") == "attachment" or e.get("trolley_configuration"):
            e["product_family"] = "brush_cutter_trolley_attachment"
            e["need_type"] = "attachment"
        else:
            e["family_hint"] = "trolley_ambiguous"
    elif (cutter or cutter_use) and not e.get("owns_brush_cutter"):
        e["product_family"] = "brush_cutter_machine"
        if cutter_use and not cutter:
            e["family_inferred_from"] = "cutting grass / weeds / bushes"
    if re.search(r"\bother products?\b|\bsomething else\b", t):
        e["family_hint"] = "other"

    # ── Wheel barrow tipping requirement (with negation) ──
    if re.search(r"\b(don'?t|do not|dont|no) (need|want) (a |the )?(dumper|tipping|tipper)|\bwithout (a )?(dumper|tipping)\b|"
                 r"\bno dumper\b|\bnot the (dumper|tipping)\b|\b(normal|standard|regular|simple|plain|basic) "
                 r"(one|wheel barrow|barrow|type|model)\b|\bstandard is fine\b", t):
        e["dumper_required"] = False
    elif re.search(r"\b(dumper|tipping|tipper|dumps?|dumping|easy (unloading|emptying)|"
                   r"tips?( it)?( over| out)? easily|tip (it )?over|empt(y|ies|ying) (it )?(easily|quickly|fast)|"
                   r"easily (tip|empty|unload)|easy to (tip|empty|unload)|tilt(s|ing)?( it)?|"
                   r"tip( \w+){0,2} (out|over|quickly|easily|fast))\b", t):
        e["dumper_required"] = True

    # ── Push-type trolley for complete machines ──
    if re.search(r"\b(no trolley|without (a )?trolley|handheld|hand held|carry it myself)\b", t):
        e["needs_push_trolley"] = False
    elif wants_wheels and e.get("product_family") == "brush_cutter_machine":
        e["needs_push_trolley"] = True

    # ── Warranty: requirement, preference, or indifference ──
    declined: list[str] = []
    for m in re.finditer(r"\b(?:don'?t|do not|dont) (?:really )?(?:care|mind|bother) (?:much |too much )?(?:about|for) "
                         r"(?:the )?(\w+)|\b(\w+) (?:doesn'?t|does not|don'?t) matter|\b(\w+) (?:is )?not (?:that |very )?important|"
                         r"\bno need (?:of|for) (?:a |any )?(\w+)", t):
        word = next(g for g in m.groups() if g)
        slot = DECLINE_TOPICS.get(word)
        if slot:
            declined.append(slot)
    if declined:
        e["declined_slots"] = declined

    if "warranty_min_months" not in declined and re.search(r"\bwarranty\b", t):
        asks_about = re.search(r"\b(how much|what is|what's|does it|is there|any|which)\b", t) \
            and not RE_PRODUCT_SEARCH.search(t)
        if re.search(r"\b(longest|best|maximum|max|most|more|better|long|good|strong)\b[\w\s]{0,12}\bwarranty\b|"
                     r"\bwarranty\b[\w\s]{0,10}\b(longest|best|most)\b", t):
            e["warranty_preference"] = "longest"
        if re.search(r"\b(at least|minimum|min|must|need|needed|required|important|matters?|want|with|reliable)\b", t) \
                and not (asks_about and "?" in t):
            if re.search(r"\b(1|a|one) ?(year|yr)s?\b|\b12 months?\b", t):
                e["warranty_min_months"] = 12
            elif re.search(r"\b6 ?months?\b", t):
                e["warranty_min_months"] = 6
            elif "warranty_preference" not in e:
                e["warranty_min_months"] = 1

    # ── Running-time preference: long working hours / large areas ──
    if re.search(r"\b(long hours|long time|longer (running|run|hours)|longest[\s-]?running|runs? (the )?longest|"
                 r"all day|full day|whole day|continuous(ly)?|non[\s-]?stop|many hours|hours (of|at a) (work|stretch)|"
                 r"long[\s-]running|heavy[\s-]duty use|heavy use|daily use|commercial use|professional use)\b", t):
        e["run_time_preference"] = "longest"
    ma = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:acres?|acre)\b", t)
    if ma:
        e["area_acres"] = float(ma.group(1))
        if e["area_acres"] >= 3 and "run_time_preference" not in e:
            e["run_time_preference"] = "longest"
            e["run_time_reason"] = f"{ma.group(1)} acres"

    # ── Carburetor preference ──
    if re.search(r"\bwalbro\b", t) and re.search(r"\b(prefer|want|like|need|carburetor)\b", t):
        e["carburetor_preference"] = "walbro"
    elif re.search(r"\bstable carburetor\b", t):
        e["carburetor_preference"] = "stable"

    # ── Machine make / model for fitment ──
    m = re.search(BRANDS + r"[\s-]*([a-z]{0,4}[\s-]?\d{2,4}[a-z]?)?", t)
    if m and not re.search(r"\bsgb\b", m.group(0)):
        e["machine_model"] = m.group(0).strip().upper()
    else:
        m2 = re.search(r"\bmodel (is |number )?([a-z0-9-]{3,12})\b", t)
        if m2 and m2.group(2) not in ("number", "fit", "the"):
            e["machine_model"] = m2.group(2).upper()

    # ── Budget ──
    for pat in (r"\brs\s*(\d[\d,]*)", r"(\d[\d,]*)\s*(?:rs\b|rupees|inr|/-)",
                r"(?:budget|price limit|spending limit|my limit|limit|max(?:imum)? price|price range|range)"
                r"(?: is| of| around| about| near| upto| up to|\s*[:=-])*(?: only| around| about| roughly| max)*\s*(\d[\d,]*)",
                r"\b(?:under|below|within|upto|up to|max(?:imum)?|less than|not more than|no more than|around|about)\s*(\d[\d,]*)"
                r"(?!\s*" + NON_PRICE_UNITS + ")",
                r"\b(?:spend|pay|afford|invest)(?: only| up to| upto| around| about| more than| over| above| max)?\s*(\d[\d,]*)"
                r"(?!\s*" + NON_PRICE_UNITS + ")"):
        mb = re.search(pat, t)
        if mb:
            n = _num(mb.group(1))
            if n and n >= 500:
                e["budget"] = n
                break

    # ── Quantity ──
    for pat in (r"\b(\d{1,3})\s*(?:pieces?|pcs|units?|nos\b|numbers?|trolleys?|barrows?|wheel barrows?|machines?|"
                r"brush cutters?|cutters?|sets?)\b",
                r"\b(\d{1,3}) of (?:the|them|these|those|your)\b",
                r"\b(?:quantity|qty)\s*(?:to|is|=|:)?\s*(\d{1,3})\b",
                r"\b(?:need|want|buy|order|make it|change (?:it |quantity )?to|take)\s+(\d{1,3})(?!\s*(?:" + NON_PRICE_UNITS +
                r"|rs|,\d|\d))\b",
                r"\bone (?:piece|unit|trolley|barrow|machine)\b"):
        mq = re.search(pat, t)
        if mq:
            n = _num(mq.group(1)) if mq.groups() else 1
            if n and 0 < n <= 500:
                e["quantity"] = n
                break
    if "add one more" in t or "1 more" in t:
        e["quantity_delta"] = 1

    # ── Required capacity (wheel barrows) ──
    mc = re.search(r"\b(\d{2,3})\s*(?:l|ltr|litres?|liters?)\b", t)
    if mc and (e.get("product_family") == "wheel_barrow" or "barrow" in t or "capacity" in t):
        e["capacity_required_litres"] = int(mc.group(1))

    # ── Intended use ──
    uses = [u for u in ("soil", "sand", "manure", "compost", "farm produce", "construction", "cement", "bricks",
                        "gravel", "fodder", "fertiliser", "fertilizer", "garden", "grass", "weeds", "bushes",
                        "orchard", "sugarcane", "paddy") if re.search(rf"\b{u}\b", t)]
    if uses:
        e["intended_use"] = ", ".join(uses)

    # ── Fact topics ──
    topics = [name for name, pat in FACT_TOPIC_PATTERNS if re.search(pat, t)]
    if "load_capacity" in topics and "capacity" in topics:
        topics.remove("capacity")
    if topics:
        e["fact_topics"] = topics

    # Echo the customer's own words when the product line was inferred from their use case.
    if e.get("family_inferred_from"):
        if e["product_family"] == "wheel_barrow":
            mats = [m.group(0) for m in re.finditer(rf"\b{BARROW_MATERIALS}\b", t)]
            e["family_inferred_from"] = "moving " + _join(dict.fromkeys(mats))
        else:
            tg = [m.group(0) for m in re.finditer(rf"\b{CUTTER_TARGETS}\b", t)]
            e["family_inferred_from"] = "clearing " + _join(dict.fromkeys(tg))

    if RE_CORRECTION.search(t):
        e["is_correction"] = True
    for slot in declined:                     # "X doesn't matter" wins over any value guessed for X
        e.pop(slot, None)
    return e
