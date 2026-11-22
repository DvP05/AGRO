"""Open catalogue questions a customer asks in their own words.

The fact gate answers "what is the warranty of X". People also ask things like:
  • capability   — "can the walbro cut sugarcane?", "is the dumper good for a construction site?"
  • superlatives — "which brush cutter runs longest?", "which one is lightest?", "most powerful?"
  • ease of use  — "can a woman use it easily?", "is it hard to push?", "will my back hurt?"

Each answer is reasoned *only* from catalogue facts that passed the evidence gate. When the catalogue is
silent, the answer says so plainly and says what the catalogue does state, so the customer still gets help.
"""

import re

from app.nlu.normalize import normalize

CUT_VERBS = r"(cut|cutting|clear|clearing|trim|trimming|mow|mowing|slash|remove|chop)"
CARRY_VERBS = r"(carry|carrying|move|moving|shift|shifting|transport|haul|take|lift|hold|load)"

RE_CAPABILITY = re.compile(
    r"\b(can|could|will|would|does|do|is|are)\b[\w\s']{0,40}?\b(be used for|used for|good for|suitable for|useful for|"
    r"meant for|work (on|for|in)|handle|manage|" + CUT_VERBS + r"|" + CARRY_VERBS + r")\b(?P<obj>[\w\s,/&-]{2,60})")
RE_SUITABLE = re.compile(r"\b(good|suitable|useful|right|ok|okay|fine|enough) for (?P<obj>[\w\s,/&-]{2,60})")

SUPERLATIVES = {
    "weight_light": r"\b(lightest|least heavy|light ?weight|less heavy|weighs? (the )?least)\b",
    "weight_heavy": r"\b(heaviest)\b",
    "power": r"\b(most powerful|more powerful|highest power|strongest engine|most hp|more hp|powerful)\b",
    "run_time": r"\b(runs? (the )?longest|longest[\s-]?running|longest run(ning)? time|runs? (for )?more hours|"
                r"most running time|run longer|lasts? longest)\b",
    "warranty": r"\b(longest warranty|best warranty|most warranty|more warranty|highest warranty|maximum warranty)\b",
    "capacity": r"\b(biggest|largest|most capacity|carr(y|ies) (the )?most|holds? (the )?most)\b",
}
RE_WHICH = re.compile(r"\b(which|what|who)\b|\b(is|are) there\b|\bbest\b")

RE_EASE = re.compile(r"\b(easy to (use|operate|handle|push|start|carry|run|manage)|(use|operate|handle|push|run)\b[\w ]{0,30}\beasily|"
                     r"(woman|women|lady|ladies|elderly|old (man|person|farmer)|beginner|kid)s? (can )?(use|operate|handle|run)|"
                     r"(father|mother|dad|mom|grand(father|mother|pa|ma)|wife|husband|senior|aged)\b[\w ]{0,30}\b(use|operate|handle|run|push)|"
                     r"simple to (use|operate)|comfortable to (use|hold)|tiring|back ?pain|shoulder pain|"
                     r"for (a |an )?(woman|women|lady|ladies|old|older|elderly|aged|beginner|beginners|first[- ]timer|kid)|"
                     r"without experience|no experience|hard to (use|operate|push|start|handle|carry)|"
                     r"difficult to (use|operate|push|start|handle|carry)|heavy to (carry|use|push|hold)|"
                     r"(old|elderly|aged) (man|person|people|farmer|father)|user[- ]friendly)\b")


def detect(text: str, entities: dict, st: dict) -> str | None:
    t = normalize(text)
    topics = entities.get("fact_topics") or []
    if any(x in topics for x in ("exact_fitment", "load_capacity", "capacity", "price", "discount")):
        sup = _superlative(t)
        return "SUPERLATIVE" if sup in ("capacity",) and RE_WHICH.search(t) else None
    if RE_EASE.search(t):
        return "EASE_OF_USE"
    sup = _superlative(t)
    asking_to_buy = re.search(r"\b(give me|i want|i need|i'?d like|show me|suggest|recommend|looking for|get me)\b", t)
    if sup and RE_WHICH.search(t) and not asking_to_buy:   # "give me the one with the best warranty" → recommend
        return "SUPERLATIVE"
    question = "?" in text or re.match(r"(can|could|will|would|does|do|is|are)\b", t)
    if question and (RE_CAPABILITY.search(t) or RE_SUITABLE.search(t)):
        if entities.get("mentioned_product") or st.get("selected_product_id") or st.get("fact_focus_id") \
                or entities.get("product_family"):
            return "CAPABILITY"
    return None


def _superlative(t: str) -> str | None:
    for key, pat in SUPERLATIVES.items():
        if re.search(pat, t):
            return key
    return None


def _subjects(m, st: dict, e: dict) -> list[dict]:
    store = m.store
    pids = e.get("mentioned_products") or e.get("variant_products")
    if pids:
        return [store.get_product(pid) for pid in pids]
    fam = e.get("product_family")
    if fam:
        return store.get_family(fam)
    for pid in (st.get("fact_focus_id"), st.get("selected_product_id")):
        if pid:
            return [store.get_product(pid)]
    fam = st["fingerprint"].get("product_family")
    return store.get_family(fam) if fam else []


def _fact(m, pid: str, field: str) -> dict | None:
    c = m.evidence_gate.resolve(pid, field)
    return c if c["evidence_status"] == "SUPPORTED" else None


def _num(v: str, unit: str) -> float | None:
    mm = re.search(rf"(\d+(?:\.\d+)?)\s*{unit}", str(v).lower())
    return float(mm.group(1)) if mm else None


def respond(m, sid: str, st: dict, act: str, text: str, e: dict, r: dict) -> bool:
    if act == "CAPABILITY":
        return _capability(m, sid, st, text, e, r)
    if act == "SUPERLATIVE":
        return _superlative_answer(m, sid, st, text, e, r)
    if act == "EASE_OF_USE":
        return _ease(m, sid, st, text, e, r)
    return False


def _finish(m, r: dict, st: dict, sid: str, reply: str, claims: list[dict], act: str, qr: list[str], products=()):
    from app.dialogue.manager import _image_view
    if claims:
        m._set_receipt(st, claims, sid)
    r["images"] = [i for i in (_image_view(p) for p in list(products)[:2]) if i]
    r.update(reply=reply, next_action="ANSWER_WITH_EVIDENCE", quick_replies=qr)
    r["conversation_act"] = act
    return True


def _capability(m, sid, st, text, e, r) -> bool:
    t = normalize(text)
    mm = RE_CAPABILITY.search(t) or RE_SUITABLE.search(t)
    obj = re.sub(r"\b(it|this|that|them|my|our|the|a|an|easily|properly|well|please)\b", " ", mm.group("obj"))
    obj = re.sub(r"\s+", " ", obj).strip(" ?.,") or "that"
    wants_cut = bool(re.search(rf"\b{CUT_VERBS}\b", t))
    wants_carry = bool(re.search(rf"\b{CARRY_VERBS}\b", t))
    subjects = _subjects(m, st, e)[:3]
    if not subjects:
        return False
    lines, claims = [], []
    from app.decision.fit import USE_SYNONYMS
    if wants_cut and all(p["family"] == "wheel_barrow" for p in subjects):
        return _finish(m, r, st, sid, "No, wheel barrows are for carrying loads, not cutting. For grass and weeds "
                       "you'd want one of SGB's brush cutters (from ₹14,000). Shall I show you those?", [],
                       "CAPABILITY", ["Show brush cutters", "Talk to sales team"])
    for p in subjects:
        uses = _fact(m, p["product_id"], "described_uses")
        ptype = _fact(m, p["product_id"], "product_type") or _fact(m, p["product_id"], "operation")
        cls = "cutter" if p["family"] in ("brush_cutter_machine",) else \
            "trolley" if p["family"] == "brush_cutter_trolley_attachment" else "barrow"
        name = f"**{p['short_name']}**"
        if wants_cut and cls == "barrow":
            lines.append(f"{name}: no, a wheel barrow is for carrying loads, not cutting. For cutting you'd want "
                         "one of SGB's brush cutters.")
            continue
        if wants_carry and cls == "cutter" and not re.search(r"\b(grass|weed)", obj):
            lines.append(f"{name}: no, it's a brush cutter for cutting grass and weeds, not for carrying loads. "
                         "A wheel barrow does that job.")
            continue
        if cls == "trolley":
            op = _fact(m, p["product_id"], "operation")
            claims += [c for c in (op,) if c]
            lines.append(f"{name} isn't a cutter itself. It {op['rendered_value'] if op else 'carries your brush cutter on wheels'} "
                         f"[p.{p['price']['source']['page']}], so what it can cut depends on your machine.")
            continue
        words = [w for w in re.findall(r"[a-z]+", obj) if len(w) > 2]
        hay = " ".join(str(c["rendered_value"]).lower() for c in (uses, ptype) if c)
        # Material synonyms (cement → construction materials) count; crop names never become "agricultural".
        syn = {k: v for k, v in USE_SYNONYMS.items() if not v.startswith("agricultural")}
        hit = [w for w in words if w in hay or syn.get(w, "§").split()[0] in hay
               or (w.endswith("s") and w[:-1] in hay)]
        if uses:
            claims.append(uses)
        if hit:
            lines.append(f"{name}: yes. The catalogue describes it for {uses['rendered_value'] if uses else hay} "
                         f"[p.{p['price']['source']['page']}].")
        elif uses:
            lines.append(f"{name}: the catalogue describes it for {uses['rendered_value']} [p.{p['price']['source']['page']}]. "
                         f"It doesn't mention {obj} specifically, so I can't promise it. The sales team can "
                         "confirm before you buy.")
        else:
            lines.append(f"{name}: the catalogue doesn't list its uses in that detail, so I can't confirm {obj}. "
                         "The sales team can check.")
    if re.search(r"\b(sugarcane|cane|stalks?|thick|wood|branches|trees?)\b", obj) and any(
            p["family"] == "brush_cutter_machine" for p in subjects):
        lines.append("\nThick stalks or woody growth depend on the cutting blade used, and the catalogue doesn't "
                     "list blades, so it's worth checking with the sales team.")
    reply = "\n\n".join(lines) if len(lines) > 1 else lines[0]
    one = subjects[0] if len(subjects) == 1 else None
    if one:
        st["fact_focus_id"] = st["selected_product_id"] = one["product_id"]
    return _finish(m, r, st, sid, reply, claims, "CAPABILITY",
                   (["Show order preview"] if one else ["Compare options"]) + ["Talk to sales team"], subjects)


def _superlative_answer(m, sid, st, text, e, r) -> bool:
    t = normalize(text)
    key = _superlative(t)
    store = m.store
    pool = _subjects(m, st, e)
    if len(pool) < 2:
        fam = e.get("product_family") or st["fingerprint"].get("product_family")
        if fam:
            pool = store.get_family(fam)
        elif key in ("power", "run_time", "warranty"):
            pool = store.get_family("brush_cutter_machine")
        elif key == "capacity":
            pool = store.get_family("wheel_barrow")
        else:
            pool = store.get_all_products()
    field = {"weight_light": "weight", "weight_heavy": "weight", "power": "power", "run_time": "run_time",
             "warranty": "engine_warranty", "capacity": "capacity"}[key]
    claims = [m.evidence_gate.resolve(p["product_id"], field) for p in pool]
    known = [(p, c) for p, c in zip(pool, claims) if c["evidence_status"] == "SUPPORTED"]
    label = {"weight": "weight", "power": "power", "run_time": "running time", "engine_warranty": "engine warranty",
             "capacity": "capacity"}[field]
    group = {"wheel_barrow": "wheel barrows", "brush_cutter_machine": "brush cutters",
             "brush_cutter_trolley_attachment": "trolleys"}.get(pool[0]["family"], "products") \
        if len({p["family"] for p in pool}) == 1 else "products"

    if not known:
        unconf = [c for c in claims if c["evidence_status"] in ("UNAPPROVED", "CONFLICTED")]
        extra = (" Some figures appear only in marketing tags or artwork, and SGB hasn't approved them, so I won't "
                 "compare on them.") if unconf else ""
        reply = (f"I'm afraid SGB's catalogue doesn't list the {label} of its {group}, so I can't tell you which is "
                 f"{'lightest' if key == 'weight_light' else 'best'} on that.{extra} The sales team can check it for you.")
        if key in ("weight_light", "weight_heavy") and pool[0]["family"] == "brush_cutter_machine":
            reply += ("\n\nIf the worry is carrying the machine, the **Varsha** comes on a push-type trolley, so you "
                      "push it instead of carrying it.")
        return _finish(m, r, st, sid, reply, claims, "SUPERLATIVE", ["Talk to sales team", "Compare options"])

    def score(c):
        v = c["rendered_value"]
        if field == "engine_warranty":
            return 0 if str(v).lower().startswith("no") else (_num(v, "year") or 0) * 12 + (_num(v, "month") or 0)
        if field == "run_time":
            return _num(v, "hour") or 0
        if field == "power":
            return _num(v, "hp") or 0
        return _num(v, "") or 0
    ranked = sorted(known, key=lambda pc: score(pc[1]), reverse=key != "weight_light")
    best = ranked[0]
    ties = [p for p, c in ranked if score(c) == score(best[1])]
    missing = [p["short_name"] for p, c in zip(pool, claims) if c["evidence_status"] != "SUPPORTED"]
    if len(ties) == len(known) and len(known) > 1:
        reply = (f"They're the same on that: all listed {group} show **{best[1]['rendered_value']}** {label} "
                 f"[catalogue]. So {label} won't separate them. Warranty, running time and price are what differ.")
    else:
        others = "; ".join(f"{p['short_name']} {c['rendered_value']}" for p, c in ranked[1:])
        reply = (f"The **{best[0]['short_name']}** has the {'longest' if field in ('run_time', 'engine_warranty') else 'highest'} "
                 f"{label}: **{best[1]['rendered_value']}** [p.{best[1]['source_page']}]."
                 + (f" For comparison: {others}." if others else ""))
        if best[1].get("qualifier") == "as provided":
            reply += " (Running times are marked \"as provided\" in the catalogue, not guaranteed.)"
        st["fact_focus_id"] = st["selected_product_id"] = best[0]["product_id"]
        st["last_list"] = [p["product_id"] for p, _ in ranked]
    if missing:
        reply += f"\n\nNot stated in the catalogue for: {', '.join(missing)}."
    reply += f"\n\nWant me to {'prepare an order preview for it' if len(ties) == 1 else 'compare them in full'}?"
    return _finish(m, r, st, sid, reply, [c for _, c in known], "SUPERLATIVE",
                   ["Show order preview", "Compare options"] if len(ties) == 1 else ["Compare options"],
                   [best[0]] if len(ties) == 1 else [])


def _ease(m, sid, st, text, e, r) -> bool:
    subjects = _subjects(m, st, e)
    fam = subjects[0]["family"] if subjects else (st["fingerprint"].get("product_family") or "brush_cutter_machine")
    store = m.store
    claims, lines = [], []
    if fam in ("brush_cutter_machine", "brush_cutter_trolley_attachment"):
        varsha = store.get_product("VARSHA-TROLLEY")
        vt = _fact(m, "VARSHA-TROLLEY", "product_type")
        side = _fact(m, "BCT-SIDE", "benefit") or _fact(m, "BCT-SIDE", "operation")
        claims += [c for c in (vt, side) if c]
        lines.append("Honest answer: the catalogue doesn't list the weight of the machines, so I can't say how heavy "
                     "they feel.")
        lines.append("What it does say helps here:")
        if vt:
            lines.append(f"• **{varsha['short_name']}** is a {vt['rendered_value']} [p.4], so you push it on wheels "
                         "instead of carrying it on your body.")
        if side:
            lines.append(f"• If you already own a brush cutter, SGB's **trolleys** {side['rendered_value']} [p.1].")
        lines.append("\nFor someone who finds carrying tiring, a push-type setup is usually the easier choice. The "
                     "sales team can tell you the actual weight and how the controls work.")
        qr = ["Tell me about Varsha", "Talk to sales team"]
    else:
        d = _fact(m, "WB-DUMPER", "tipping_mechanism")
        claims += [c for c in (d,) if c]
        lines.append("The catalogue doesn't list the wheel barrows' weight or handling, so I can't promise how easy "
                     "they are to push.")
        if d:
            lines.append(f"One thing that does reduce effort: the **Dumper Wheel Barrow** has a {d['rendered_value']} "
                         "[p.2], so it tips the load out and you don't have to lift it to empty it.")
        qr = ["Tell me about Dumper", "Talk to sales team"]
    return _finish(m, r, st, sid, "\n".join(lines), claims, "EASE_OF_USE", qr)
