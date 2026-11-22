"""Dialogue manager — the per-turn decision loop.

message → NLU → Need Fingerprint update → candidate evaluation → AskSmart /
recommendation → Truth Receipt → policy gates → template reply → decision trace.
"""

import re
import time
import uuid
from typing import Any

from app.catalogue.evidence import SUMMARY_FIELDS, EvidenceGate
from app.catalogue.ingest import CatalogueStore
from app.catalogue.retrieval import CatalogueRetriever
from app.decision.fit import ELIGIBLE, EXCLUDED, UNVERIFIED, FitEngine
from app.dialogue import answers, conversation
from app.dialogue.need_fingerprint import FAMILY_SLOTS, NeedFingerprint
from app.dialogue.question_value import QuestionValueEngine
from app.handoff import service as handoff
from app.nlu.normalize import normalize
from app.nlu.predict import RE_CANCEL, classify_intent, extract_entities
from app.sales import checkout, orders, responses
from app.sales.dealguard import DealGuard
from app.storage import database as db

STATES = ["NEW", "DISCOVERY", "CLARIFYING", "RECOMMENDED", "HUMAN_REVIEW", "QUOTE_REVIEW",
          "ORDER_REVIEW", "ORDER_CONFIRMED", "HUMAN_OWNED", "CLOSED"]

OUT_OF_CATALOGUE = re.compile(r"\b(tractors?|harvesters?|sprayers?|pumps?|tillers?|drones?|rotavators?|"
                              r"seeds?|fertili[sz]ers? for sale|chainsaws?|generators?|lawn ?mowers?)\b")
FAMILY_WORDS = {"wheel barrow": "wheel_barrow", "brush cutter machine": "brush_cutter_machine",
                "brush cutter trolley": None}

SLOT_FROM_ENTITY = ["product_family", "need_type", "trolley_configuration", "machine_model", "dumper_required",
                    "capacity_required_litres", "intended_use", "needs_push_trolley", "warranty_min_months",
                    "warranty_preference", "carburetor_preference", "run_time_preference", "budget", "quantity"]


class DialogueManager:
    def __init__(self, store: CatalogueStore):
        self.store = store
        self.retriever = CatalogueRetriever(store)
        self.evidence_gate = EvidenceGate(store)
        self.fit = FitEngine(store)
        self.asksmart = QuestionValueEngine()
        self.dealguard = DealGuard()
        self.alias_index = {}
        for p in store.get_all_products():
            for a in p["aliases"] + [p["short_name"].lower()]:
                self.alias_index[a] = p["product_id"]
        self.sessions: dict[str, dict] = {}

    # ── Session state ────────────────────────────────────────

    def state(self, session_id: str) -> dict:
        if session_id not in self.sessions:
            self.sessions[session_id] = {
                "fingerprint": NeedFingerprint(session_id), "lead_status": "NEW", "turn": 0,
                "family_hint": None, "selected_product_id": None, "fact_focus_id": None,
                "last_recommendation": None, "last_receipt": None, "receipts": [],
                "order_preview": None, "order": None, "handoff": None, "bot_paused": False,
                "owner": {"type": "bot"}, "opted_out": False, "follow_up_pending": False,
                "last_question": None, "asked_slots": [], "declined_slots": [], "questions_since_rec": 0,
                "last_dealguard": None, "summary_text": "", "last_list": [], "said": {},
            }
        return self.sessions[session_id]

    # Backwards-compatible alias used by older tests / routes.
    _get_session_state = state

    # ── Turn processing ──────────────────────────────────────

    def process_message(self, session_id: str, text: str, client_message_id: str | None = None,
                        bound_preview_id: str | None = None, channel: str = "browser") -> dict:
        """`bound_preview_id`: a confirmation tapped on a specific preview (WhatsApp button) may only confirm
        that exact preview version — never a newer one the customer hasn't seen."""
        if client_message_id:
            cached = db.get_processed(session_id, client_message_id)
            if cached:
                return {**cached, "duplicate_delivery": True}

        t0 = time.perf_counter()
        st = self.state(session_id)
        st["channel"] = channel
        st["bound_preview_id"] = bound_preview_id
        fp: NeedFingerprint = st["fingerprint"]
        st["turn"] += 1
        msg_id = f"msg_{uuid.uuid4().hex[:10]}"
        state_before, rev_before = st["lead_status"], fp.revision

        entities = extract_entities(text, self.alias_index)
        self._contextual_entities(st, text, entities)
        intent, confidence, nlu_trace = classify_intent(text, entities)
        # "Can the walbro cut sugarcane?" asks; it must not record sugarcane as a requirement.
        st["qa_act"] = answers.detect(text, entities, st) if not st["bot_paused"] else None
        # A multi-item order names exact products; its lines must not become one customer's "need".
        pv = st["order_preview"]
        editing_cart = bool(pv and pv.get("items") and pv["status"] == "PENDING_CONFIRMATION"
                            and checkout.update_cart_item(text, pv["items"], self.alias_index) is not None)
        if not st["qa_act"] and not st["order"] and (editing_cart or checkout.parse_cart(text, self.alias_index)):
            st["qa_act"] = "CART"
        st["nlu_rule"] = nlu_trace.get("rule")      # rule-backed intents outrank conversational acts
        db.save_message(msg_id, session_id, "user", text, intent, confidence)

        result: dict[str, Any] = {"reply": None, "quick_replies": [], "product_cards": [], "images": [], "next_action": None,
                                  "receipt": None, "question_scores": None, "side_effects": []}
        changes: list[dict] = []

        if st["bot_paused"]:
            result["next_action"] = "HUMAN_OWNS_CONVERSATION"
            result["side_effects"].append("bot_reply_suppressed")
            db.log_event(session_id, "INBOUND_WHILE_HUMAN_OWNED", {"message_id": msg_id})
        else:
            changes = self._apply_entities(st, fp, entities, intent, msg_id, text)
            self._decide(session_id, st, fp, intent, entities, changes, text, msg_id, result)

        reply_id = None
        if result["reply"]:
            reply_id = f"msg_{uuid.uuid4().hex[:10]}"
            db.save_message(reply_id, session_id, "agent", result["reply"], intent, confidence)

        st["summary_text"] = self._summary(st, fp)
        db.update_session(session_id, lead_status=st["lead_status"], need_fingerprint=_json(fp.to_dict()),
                          summary=st["summary_text"])

        rec = st["last_recommendation"] if result.get("candidates_changed") else None
        trace = self._trace(session_id, st, msg_id, text, intent, confidence, nlu_trace, entities, changes,
                            rev_before, state_before, result, rec, t0)
        db.save_trace(trace)

        payload = {
            "conversation_id": session_id, "message_id": msg_id, "reply_id": reply_id,
            "reply": result["reply"], "quick_replies": result["quick_replies"],
            "product_cards": result["product_cards"], "images": result.get("images", []), "state": st["lead_status"], "lead_status": st["lead_status"],
            "state_revision": fp.revision, "next_action": result["next_action"],
            "intent": intent, "confidence": confidence, "nlu": nlu_trace, "entities": entities,
            "changes": changes, "fingerprint": fp.to_dict(),
            "candidates": self._candidate_view(st["last_recommendation"]),
            "receipt": st["last_receipt"], "question_scores": result["question_scores"],
            "dealguard": st["last_dealguard"], "order_preview": st["order_preview"], "order": st["order"],
            "handoff": _handoff_view(st["handoff"]),
            "flags": {"bot_paused": st["bot_paused"], "opted_out": st["opted_out"],
                      "follow_up_pending": st["follow_up_pending"]},
            "trace_id": trace["trace_id"], "conversation_act": result.get("conversation_act"),
        }
        if client_message_id:
            db.save_processed(session_id, client_message_id, payload)
        return payload

    # ── Entity → Need Fingerprint ────────────────────────────

    def _contextual_entities(self, st: dict, text: str, e: dict) -> None:
        """Interpret short answers in the context of the last question asked."""
        t = normalize(text)
        lq =(st.get("last_question") or {}).get("slot")
        neg = re.search(r"\b(doesn'?t matter|not important|no preference|any(one)? is fine|either|no)\b", t)
        if lq == "warranty_min_months" and "warranty_min_months" not in e:
            if re.search(r"\b(1|one) ?year|12\b", t):
                e["warranty_min_months"] = 12
            elif re.search(r"\b6\b|six", t):
                e["warranty_min_months"] = 6
            elif neg:
                e["declined_slot"] = "warranty_min_months"
        elif lq == "carburetor_preference" and "carburetor_preference" not in e:
            if "walbro" in t:
                e["carburetor_preference"] = "walbro"
            elif "stable" in t:
                e["carburetor_preference"] = "stable"
            elif neg:
                e["declined_slot"] = "carburetor_preference"
        elif lq == "needs_push_trolley" and "needs_push_trolley" not in e:
            if re.search(r"\b(regular|normal|without|handheld|no)\b", t):
                e["needs_push_trolley"] = False
            elif re.search(r"\b(push|trolley|yes)\b", t):
                e["needs_push_trolley"] = True
        elif lq == "dumper_required" and "dumper_required" not in e:
            if re.search(r"\b(yes|tipping|dump)\b", t):
                e["dumper_required"] = True
            elif re.search(r"\b(no|standard|normal|fine)\b", t):
                e["dumper_required"] = False
        elif lq == "budget" and "budget" not in e:
            m = re.search(r"\b(\d[\d,]{2,})\b", t)
            if m:
                e["budget"] = int(m.group(1).replace(",", ""))
        elif lq == "product_family" and "product_family" not in e:
            if "other" in t:
                e["family_hint"] = "other"
        if st.get("order_preview") and "quantity" not in e:
            m = re.fullmatch(r"(\d{1,3})( (pieces?|units?|nos))?", t)
            if m:
                e["quantity"] = int(m.group(1))
        # An owner who has not yet named a family is asking about the trolley attachment.
        fam_now = st["fingerprint"].get("product_family")
        if e.get("wants_wheels") and fam_now == "brush_cutter_machine" and not e.get("owns_brush_cutter") \
                and "needs_push_trolley" not in e:
            e["needs_push_trolley"] = True       # "I'd rather push it around than carry it"
        if e.get("need_type") == "attachment" and "product_family" not in e and \
                (fam_now in (None, "brush_cutter_trolley_attachment") or st.get("family_hint") == "trolley_ambiguous"):
            e["product_family"] = "brush_cutter_trolley_attachment"
        if e.get("need_type") == "complete_machine" and "product_family" not in e and \
                (st.get("family_hint") == "trolley_ambiguous" or fam_now == "brush_cutter_trolley_attachment"):
            e["product_family"] = "brush_cutter_machine"
            e["needs_push_trolley"] = True
        if e.get("mentioned_product") and "product_family" not in e and fam_now is None:
            e["product_family"] = self.store.get_product(e["mentioned_product"])["family"]
        if OUT_OF_CATALOGUE.search(t) and not e.get("product_family") and not e.get("mentioned_product"):
            e["out_of_catalogue"] = True

    def _apply_entities(self, st: dict, fp: NeedFingerprint, e: dict, intent: str, msg_id: str, text: str) -> list[dict]:
        if e.get("family_hint"):
            st["family_hint"] = e["family_hint"]
        for slot in ([e["declined_slot"]] if e.get("declined_slot") else []) + e.get("declined_slots", []):
            if slot not in st["declined_slots"]:
                st["declined_slots"].append(slot)
        if e.get("family_inferred_from") and not fp.get("product_family"):
            st["inferred_note"] = e["family_inferred_from"]
        # Questions and comparisons never rewrite requirements.
        if intent in ("ASK_PRODUCT_FACT", "COMPARE_PRODUCTS", "REQUEST_DISCOUNT", "CUSTOMER_SUPPORT") or st.get("qa_act"):
            allowed = {"machine_model"}
        else:
            allowed = set(SLOT_FROM_ENTITY)
        conf = 0.95 if intent in ("STATE_REQUIREMENT", "CORRECT_REQUIREMENT", "SELECT_PRODUCT_FAMILY") else 0.8
        is_corr = bool(e.get("is_correction")) or intent == "CORRECT_REQUIREMENT"
        changes: list[dict] = []

        new_family = e.get("product_family")
        old_family = fp.get("product_family")
        if new_family and "product_family" in allowed and old_family and new_family != old_family:
            for slot in FAMILY_SLOTS.get(old_family, []):
                rec = fp.clear_slot(slot, msg_id, f"product family changed to {new_family}")
                if rec:
                    changes.append(rec)
            if new_family == "wheel_barrow":
                r = fp.clear_slot("need_type", msg_id, "not applicable to wheel barrows")
                if r:
                    changes.append(r)

        for key in SLOT_FROM_ENTITY:
            if key in e and key in allowed:
                rec = fp.update_slot(key, e[key], msg_id, text, conf, is_corr)
                if rec:
                    changes.append(rec)
        if fp.get("product_family") == "brush_cutter_trolley_attachment" and fp.get("need_type") is None:
            rec = fp.update_slot("need_type", "attachment", msg_id, text, 0.9)
            if rec:
                changes.append(rec)
        if e.get("quantity_delta") and fp.get("quantity"):
            rec = fp.update_slot("quantity", fp.get("quantity") + e["quantity_delta"], msg_id, text, 0.9, True)
            if rec:
                changes.append(rec)
        if fp.get("product_family"):
            st["family_hint"] = None if st["family_hint"] == "trolley_ambiguous" else st["family_hint"]
        if any(c["field"] == "machine_model" for c in changes) and re.search(r"\bmust fit|only if it fits|exact fit", text.lower()):
            rec = fp.update_slot("exact_fitment_required", True, msg_id, text, 0.9)
            if rec:
                changes.append(rec)
        return changes

    # ── Decision routing ─────────────────────────────────────

    def _decide(self, sid: str, st: dict, fp: NeedFingerprint, intent: str, e: dict, changes: list,
                text: str, msg_id: str, r: dict) -> None:
        req_changes = [c for c in changes if c["field"] != "quantity"]
        qty_change = [c for c in changes if c["field"] == "quantity"]

        # A requirement change invalidates any open preview (it was bound to the old need).
        if req_changes and st["order_preview"] and st["order_preview"]["status"] == "PENDING_CONFIRMATION":
            st["order_preview"]["status"] = "SUPERSEDED_BY_REQUIREMENT_CHANGE"
            r["side_effects"].append("order_preview_invalidated")

        if intent == "OPT_OUT":
            st["opted_out"] = True
            n = db.suppress_follow_ups(sid)
            st["follow_up_pending"] = False
            r.update(reply=responses.opted_out(), next_action="OPT_OUT_RECORDED")
            r["side_effects"].append(f"follow_ups_suppressed:{n}")
            db.log_event(sid, "OPT_OUT", {"message_id": msg_id})
            return

        if st.get("awaiting") in ("contact", "contact_area") and st["order"] and \
                self._capture_contact(sid, st, text, intent, r):
            return

        pv = st["order_preview"]
        if pv and pv.get("items") and pv["status"] == "PENDING_CONFIRMATION" and intent not in (
                "CONFIRM_ORDER", "CANCEL_OR_STOP", "ASK_PRODUCT_FACT", "COMPARE_PRODUCTS"):
            new_items = checkout.update_cart_item(text, pv["items"], self.alias_index)
            if new_items is not None:
                return self._cart_preview(sid, st, r, new_items) if len(new_items) > 1 else \
                    self._preview(sid, st, fp, r, self.store.get_product(new_items[0]["product_id"])) \
                    if new_items else r.update(reply="I've emptied the order preview. What would you like instead?",
                                               next_action="ASK_CLARIFYING_QUESTION")
        if not st["order"] and intent not in ("COMPARE_PRODUCTS", "ASK_PRODUCT_FACT", "CONFIRM_ORDER",
                                              "CANCEL_OR_STOP", "REQUEST_DISCOUNT"):
            cart = checkout.parse_cart(text, self.alias_index)
            if cart:
                return self._cart_preview(sid, st, r, [{"product_id": p, "quantity": q} for p, q in cart])

        explicit_cancel = intent == "CANCEL_OR_STOP" and RE_CANCEL.search(normalize(text))
        act = None if explicit_cancel else conversation.detect(text, intent, e, st)
        if act and (intent not in ("ASK_FOR_HUMAN", "CUSTOMER_SUPPORT") or not st.get("nlu_rule")) and \
                conversation.respond(self, sid, st, act, text, e, r):
            return

        if intent == "CANCEL_OR_STOP":
            had = bool(st["order_preview"] and st["order_preview"]["status"] == "PENDING_CONFIRMATION")
            if had:
                st["order_preview"]["status"] = "CANCELLED_BY_CUSTOMER"
                st["lead_status"] = "RECOMMENDED"
            elif not st["order"]:
                st["lead_status"] = "CLOSED"
            r.update(reply=responses.cancelled(had), next_action="CANCELLED")
            return

        if intent in ("ASK_FOR_HUMAN", "CUSTOMER_SUPPORT"):
            reason = "ASK_FOR_HUMAN" if intent == "ASK_FOR_HUMAN" else "CUSTOMER_SUPPORT"
            handoff.request_handoff(sid, st, reason, queue="support" if reason == "CUSTOMER_SUPPORT" else "sales")
            st["lead_status"] = "HUMAN_REVIEW"
            r.update(reply=responses.handoff(reason), next_action="HANDOFF_QUEUED")
            r["side_effects"].append("handoff_queued")
            return

        if e.get("out_of_catalogue"):
            r.update(reply=responses.out_of_catalogue(), next_action="OUT_OF_CATALOGUE",
                     quick_replies=["Talk to sales team"] + responses.FAMILY_REPLIES[:3])
            return

        if intent == "GREETING" and not changes:
            st["lead_status"] = "DISCOVERY" if st["lead_status"] == "NEW" else st["lead_status"]
            st["last_question"] = {"slot": "product_family"}
            r.update(reply=responses.welcome(st), next_action="ASK_CLARIFYING_QUESTION",
                     quick_replies=conversation.JOB_REPLIES + ["See all products"])
            return

        if intent == "CONFIRM_ORDER":
            return self._confirm(sid, st, fp, msg_id, r)

        if (intent == "EDIT_ORDER" or qty_change) and st["order_preview"] and st["order_preview"].get("items") and \
                st["order_preview"]["status"] == "PENDING_CONFIRMATION":
            names = ", ".join(i["name"] for i in st["order_preview"]["items"])
            r.update(reply=f"Your preview has several items ({names}). Which one should I change? For example: "
                           f"\"make the {st['order_preview']['items'][0]['name'].split()[0].lower()} 3\" or "
                           "\"remove the trolley\".", next_action="ASK_CLARIFYING_QUESTION")
            return
        if (intent == "EDIT_ORDER" or qty_change) and st["order_preview"] and \
                st["order_preview"]["status"] == "PENDING_CONFIRMATION" and not req_changes:
            return self._preview(sid, st, fp, r, self.store.get_product(st["order_preview"]["product_id"]))

        if intent in ("EXPRESS_BUYING_INTENT", "REQUEST_QUOTE"):
            return self._buy(sid, st, fp, e, r, changes)

        if intent == "REQUEST_DISCOUNT":
            product = self._subject_for_commercial(st, e)
            decision = self.dealguard.evaluate(product)
            st["last_dealguard"] = decision
            db.log_event(sid, "DEALGUARD", decision)
            if product:
                handoff.request_handoff(sid, st, "DISCOUNT_REQUEST")
                st["lead_status"] = "QUOTE_REVIEW"
                r["side_effects"].append("handoff_queued")
                claim = self.evidence_gate.resolve(product["product_id"], "price")
                self._set_receipt(st, [claim], sid)
            r.update(reply=responses.discount(product, decision), next_action="DISCOUNT_ROUTED_TO_HUMAN",
                     quick_replies=["Show order preview", "Compare options"] if product else responses.FAMILY_REPLIES[:3])
            return

        if intent == "RAISE_PRICE_OBJECTION":
            product = self._subject_for_commercial(st, e)
            value, cheaper = [], []
            if product:
                value = [c for c in self.evidence_gate.summary_claims(product["product_id"])
                         if self.evidence_gate.statable(c) and c["claim_type"] != "price"][:4]
                cheaper = [p for p in self.store.get_family(product["family"])
                           if p["price"]["amount"] < product["price"]["amount"] and self._still_viable(st, p["product_id"])]
                self._set_receipt(st, [self.evidence_gate.resolve(product["product_id"], "price")] + value, sid)
            r.update(reply=responses.objection(product, value, cheaper), next_action="HANDLE_OBJECTION",
                     quick_replies=(["Compare options"] if cheaper else []) + ["Talk to sales team"])
            return

        if intent == "REQUEST_FOLLOW_UP":
            ok = not st["opted_out"]
            if ok:
                db.save_follow_up({"follow_up_id": f"fu_{uuid.uuid4().hex[:8]}", "session_id": sid,
                                   "purpose": "customer_requested", "requested_text": text[:200],
                                   "consent_snapshot": {"opted_out": False, "requested_by_customer": True},
                                   "status": "SCHEDULED_SIMULATED"})
                st["follow_up_pending"] = True
                r["side_effects"].append("follow_up_recorded_simulated")
            r.update(reply=responses.follow_up(ok), next_action="FOLLOW_UP_RECORDED" if ok else "FOLLOW_UP_BLOCKED")
            return

        if intent == "ASK_PRODUCT_FACT":
            return self._fact(sid, st, fp, e, text, r)

        if intent == "COMPARE_PRODUCTS":
            return self._compare(sid, st, fp, e, r, text)

        if intent == "AGREEMENT" and not changes:
            p = st["order_preview"]
            if p and p["status"] == "PENDING_CONFIRMATION":
                r.update(reply=responses.agreement_needs_confirm(p), next_action="AWAIT_EXPLICIT_CONFIRMATION",
                         quick_replies=["Confirm order", "Change quantity", "Cancel"])
                return
            if st["selected_product_id"]:
                prod = self.store.get_product(st["selected_product_id"])
                r.update(reply=conversation.vary(st, "agree", [
                    f"Great! Shall I prepare an order preview for **{prod['short_name']}**, or would you like to "
                    "compare it with another option first?",
                    f"👍 Just say the word and I'll make the order preview for **{prod['short_name']}**, and tell "
                    "me the quantity if it's more than one."]), next_action="OFFER_ORDER_PREVIEW",
                         quick_replies=["Show order preview", "Compare options"])
                return

        if st["family_hint"] == "other" and not fp.get("product_family"):
            st["family_hint"] = None
            r.update(reply=responses.other_enquiry(), next_action="OTHER_ENQUIRY",
                     quick_replies=["Talk to sales team"] + responses.FAMILY_REPLIES[:3])
            return

        if intent == "UNKNOWN" and not changes and not st["last_recommendation"]:
            st["last_question"] = {"slot": "product_family"}
            r.update(reply=responses.fallback(st), next_action="ASK_CLARIFYING_QUESTION",
                     quick_replies=conversation.JOB_REPLIES)
            return
        if intent == "UNKNOWN" and not changes and st["selected_product_id"]:
            prod = self.store.get_product(st["selected_product_id"])
            r.update(reply=conversation.vary(st, "unknown_ctx", [
                f"Sorry, I didn't quite follow that. Are you asking about the **{prod['short_name']}**? You can ask "
                "me its price, warranty or specs, compare it, or ask for an order preview.",
                "I may have missed that, could you say it another way? Or I can connect you to the sales team."]),
                next_action="ASK_CLARIFYING_QUESTION",
                quick_replies=["Show order preview", "Compare options", "Talk to sales team"])
            return

        self._discover(sid, st, fp, intent, req_changes, r)

    # ── Discovery / recommendation ───────────────────────────

    def _discover(self, sid: str, st: dict, fp: NeedFingerprint, intent: str, changes: list, r: dict) -> None:
        prev_rec = st["last_recommendation"]
        prev_top = prev_rec["evaluations"][0] if prev_rec and prev_rec["evaluations"] and \
            prev_rec["evaluations"][0]["status"] != EXCLUDED else None
        rec = self.fit.recommend(fp, st["family_hint"])
        st["last_recommendation"] = rec
        r["candidates_changed"] = True
        viable = [e for e in rec["evaluations"] if e["status"] in (ELIGIBLE, UNVERIFIED)]
        # "Why not" is only useful for same-family alternatives, not for every other product line.
        excluded = [e for e in rec["evaluations"] if e["status"] == EXCLUDED and not any(
            c["criterion"] == "product_family" and c["result"] == "FAIL" for c in e["checks"])]
        viable_products = [self.store.get_product(e["product_id"]) for e in viable]

        superseded_name = None
        if prev_top and changes and (not viable or viable[0]["product_id"] != prev_top["product_id"]):
            superseded_name = prev_top["short_name"]
            if st["last_receipt"]:
                st["last_receipt"]["superseded_by"] = "pending"
        ack = responses.change_ack(changes, superseded_name)

        skip = set(st["declined_slots"])
        if st["family_hint"] == "trolley_ambiguous":
            skip.add("product_family")            # "trolley" already narrows it; ask attachment vs machine instead
        if fp.get("warranty_preference"):
            skip.add("warranty_min_months")       # "longest warranty" already answers the warranty question
        note = st.pop("inferred_note", None)
        if note and fp.get("product_family"):
            fam_label = {"wheel_barrow": "an SGB wheel barrow", "brush_cutter_machine": "an SGB brush cutter"}.get(
                fp.get("product_family"), "this product line")
            ack = (ack + "\n\n" if ack else "") + f"For {note}, {fam_label} is the right fit from our catalogue."
        scored = self.asksmart.score_all(fp, viable_products, skip)
        r["question_scores"] = _compact_scores(scored)
        next_q = next((s for s in scored if s["value"] > 0), None)

        if not viable:
            st["lead_status"] = "DISCOVERY"
            r.update(reply=(ack + "\n\n" if ack else "") + responses.no_match(excluded), next_action="NO_SAFE_MATCH",
                     quick_replies=["Change a requirement", "Talk to sales team"])
            return

        blocking = next_q and next_q["blocking"]
        # A stated preference that already separates the top candidate ("longest warranty") means: recommend now.
        prefs = [v["preference_fit"] for v in viable[:2]]
        clear_winner = len(prefs) == 2 and None not in prefs and prefs[0] > prefs[1]
        refine = next_q and len(viable) > 1 and next_q["value"] >= 0.45 and st["questions_since_rec"] < 2 \
            and intent != "DISCOVER_PRODUCT" and not clear_winner
        if blocking or refine:
            st["lead_status"] = "CLARIFYING"
            st["last_question"] = next_q
            st["asked_slots"].append(next_q["slot"])
            st["questions_since_rec"] += 1
            if next_q["slot"] == "product_family":
                r.update(reply=responses.ask(conversation.family_question(st), ack),
                         next_action="ASK_CLARIFYING_QUESTION", quick_replies=conversation.JOB_REPLIES)
                return
            r.update(reply=responses.ask(next_q["question"], ack), next_action="ASK_CLARIFYING_QUESTION",
                     quick_replies=next_q["quick_replies"])
            return

        shown = viable[:2] if len(viable) > 1 else viable
        claims = []
        for ev in shown:
            claims += self.evidence_gate.summary_claims(ev["product_id"])
        receipt = self._set_receipt(st, claims, sid, rec, shown, excluded, prev_superseded=superseded_name is not None)
        st["selected_product_id"] = shown[0]["product_id"]
        st["last_list"] = [ev["product_id"] for ev in shown]
        st["lead_status"] = "RECOMMENDED"
        st["questions_since_rec"] = 0
        st["last_question"] = None

        reply = responses.recommendation(shown, claims, excluded if fp.get("product_family") else [], ack)
        follow = None
        if shown[0]["family"] == "brush_cutter_trolley_attachment" and not fp.get("machine_model"):
            follow = ("What make and model is your brush cutter? The catalogue has no exact fitment table, "
                      "so a salesperson would confirm the fit for your machine.")
            st["last_question"] = {"slot": "machine_model"}
        elif next_q and len(viable) > 1:
            follow = f"To narrow it down: {next_q['question']}"
            st["last_question"] = next_q
        if follow:
            reply += "\n\n💡 " + follow
        qr = ["Show order preview", "Compare options"] if len(shown) > 1 else ["Show order preview"]
        if follow and st["last_question"] and st["last_question"].get("quick_replies"):
            qr = st["last_question"]["quick_replies"] + qr[:1]
        r.update(reply=reply, next_action="RECOMMEND", quick_replies=qr,
                 product_cards=[self._card(ev, claims) for ev in shown])
        r["receipt"] = receipt

    # ── Facts / comparison ───────────────────────────────────

    def _fact_subjects(self, st: dict, e: dict, text: str) -> list[dict]:
        t = text.lower()
        if e.get("mentioned_products"):
            return [self.store.get_product(pid) for pid in e["mentioned_products"]]
        if e.get("variant_products"):
            return [self.store.get_product(pid) for pid in e["variant_products"]]
        if re.search(r"\b143 ?r\b|\bgl ?45\b", t):
            return [self.store.get_product("143R-STABLE"), self.store.get_product("143R-WALBRO")]
        fam = e.get("product_family")
        sel = self.store.get_product(st["selected_product_id"]) if st["selected_product_id"] else None
        if fam and (not sel or fam != sel["family"]):
            return self.store.get_family(fam)
        for pid in (st["fact_focus_id"], st["selected_product_id"]):
            if pid:
                return [self.store.get_product(pid)]
        if fam:
            return self.store.get_family(fam)
        topics = [x for x in (e.get("fact_topics") or []) if x not in ("stock", "delivery", "gst", "payment")]
        if topics:
            known = [p for p in self.store.get_all_products()
                     if any(self.evidence_gate.resolve(p["product_id"], tp)["evidence_status"] == "SUPPORTED"
                            for tp in topics)]
            if known:
                return known
        hit = self.retriever.get_product_for_query(text)
        return [hit] if hit else []

    def _fact(self, sid: str, st: dict, fp: NeedFingerprint, e: dict, text: str, r: dict) -> None:
        subjects = self._fact_subjects(st, e, text)
        topics = e.get("fact_topics") or []
        if "discount" in topics:
            return self._decide(sid, st, fp, "REQUEST_DISCOUNT", e, [], text, "", r)
        if not subjects:
            st["last_list"] = conversation.overview_ids(self.store)
            r.update(reply="Happy to tell you. Which product do you mean?\n\n" + conversation.overview_text(self.store),
                     next_action="ASK_CLARIFYING_QUESTION", quick_replies=conversation.JOB_REPLIES)
            return
        if not topics:
            topics = SUMMARY_FIELDS.get(subjects[0]["family"], ["price"])
        claims = [self.evidence_gate.resolve(p["product_id"], tpc) for p in subjects for tpc in topics]
        for i, c in enumerate(claims):
            # "Does the Varsha come with wheels?" — its product type already says it ships with a push trolley.
            if c["claim_type"] == "push_trolley" and c["evidence_status"] == "MISSING":
                pt = self.evidence_gate.resolve(c["subject_product_id"], "product_type")
                if pt["evidence_status"] == "SUPPORTED" and "trolley" in str(pt["rendered_value"]):
                    claims[i] = pt
        self._set_receipt(st, claims, sid)
        if len(subjects) == 1:
            st["fact_focus_id"] = subjects[0]["product_id"]
        else:
            st["last_list"] = [p["product_id"] for p in subjects]
        unresolved = [c for c in claims if c["evidence_status"] != "SUPPORTED"]
        r["images"] = [i for i in (_image_view(p) for p in subjects[:3]) if i]
        r.update(reply=responses.fact_answer(claims, len(subjects) > 1), next_action="ANSWER_WITH_EVIDENCE",
                 quick_replies=(["Talk to sales team"] if unresolved else []) +
                               (["Show order preview"] if st["selected_product_id"] else []))
        if unresolved:
            r["side_effects"].append("unknown_fact_disclosed")

    def _compare(self, sid: str, st: dict, fp: NeedFingerprint, e: dict, r: dict, text: str = "") -> None:
        ids = list(e.get("mentioned_products") or [])
        t = text.lower()
        # Bare model words that are too generic to be catalogue-wide aliases.
        for word, pid in (("stable", "143R-STABLE"), ("normal", "WB-NORMAL"), ("sidepack", "BCT-SIDE"),
                          ("backpack", "BCT-BACK")):
            if re.search(rf"\b{word}\b", t) and pid not in ids:
                ids.append(pid)
        if len(ids) < 2:
            rec = st["last_recommendation"]
            fam = e.get("product_family") or fp.get("product_family")
            if rec and not e.get("product_family"):
                ids = [ev["product_id"] for ev in rec["evaluations"] if ev["status"] != EXCLUDED][:3]
                if len(ids) < 2 and fam:
                    ids = [p["product_id"] for p in self.store.get_family(fam)]
            elif fam:
                ids = [p["product_id"] for p in self.store.get_family(fam)]
        if len(ids) < 2:
            r.update(reply="Which products should I compare? For example: Normal vs Dumper wheel barrow, or "
                           "143R Stable vs WALBRO.", next_action="ASK_CLARIFYING_QUESTION",
                     quick_replies=["Compare wheel barrows", "Compare brush cutters", "Compare trolleys"])
            return
        products = [self.store.get_product(pid) for pid in ids[:3]]
        fields = SUMMARY_FIELDS.get(products[0]["family"], ["price"])
        rows = []
        for f in fields:
            claims = [self.evidence_gate.resolve(p["product_id"], f) for p in products]
            rows.append((claims[0]["label"], claims))
        all_claims = [c for _, cl in rows for c in cl]
        self._set_receipt(st, all_claims, sid)
        r["images"] = [i for i in (_image_view(p) for p in products) if i]
        st["last_list"] = [p["product_id"] for p in products]
        r.update(reply=responses.comparison(products, rows, responses.comparison_take(products, rows, fp)),
                 next_action="COMPARE",
                 quick_replies=["Show order preview"] if st["selected_product_id"] else [])

    # ── Orders ───────────────────────────────────────────────

    def _subject_for_commercial(self, st: dict, e: dict) -> dict | None:
        pid = e.get("mentioned_product") or st["selected_product_id"] or st["fact_focus_id"]
        if not pid and st["order_preview"]:
            pid = st["order_preview"]["product_id"]
        return self.store.get_product(pid) if pid else None

    def _still_viable(self, st: dict, pid: str) -> bool:
        rec = st["last_recommendation"]
        if not rec:
            return True
        return pid in rec["eligible"] or pid in rec["unverified"]

    def _buy(self, sid: str, st: dict, fp: NeedFingerprint, e: dict, r: dict, changes: list | None = None) -> None:
        if st["order"]:
            r.update(reply=responses.duplicate_order(st["order"]), next_action="ORDER_ALREADY_EXISTS")
            return
        # Naming a product that contradicts a requirement the customer already stated ("I own a backpack
        # machine" → "I'll take the sidepack trolley") needs an explicit check, never a silent switch.
        conflicts = [c for c in (changes or []) if c["type"] == "correction" and c["field"] != "product_family"
                     and fp.slots[c["field"]]["kind"].startswith("HARD_CONSTRAINT") and not e.get("is_correction")]
        if conflicts:
            c = conflicts[0]
            fp.update_slot(c["field"], c["old_value"], c["source_message_id"], "reverted pending confirmation", 0.9)
            r.update(reply=f"Just to check before I prepare an order: earlier you told me **{c['label'].lower()}: "
                           f"{responses._fmt(c['old_value'])}**, but this product is for **{responses._fmt(c['new_value'])}**. "
                           f"Has that changed? Reply \"Actually, it's {responses._fmt(c['new_value'])}\" and I'll switch.",
                     next_action="CONFIRM_REQUIREMENT_CHANGE",
                     quick_replies=[f"Actually, it's {responses._fmt(c['new_value'])}", "Keep my earlier choice"])
            return
        pid = e.get("mentioned_product") or st["selected_product_id"] or st.get("fact_focus_id")
        if not pid:
            r["reply"] = "Happy to help you order. First, let me make sure we pick the right product."
            self._discover(sid, st, fp, "DISCOVER_PRODUCT", [], r)
            r["reply"] = "Happy to help you order — first let me make sure it's the right product.\n\n" + r["reply"]
            return
        rec = st["last_recommendation"]
        if not rec or rec["fingerprint_revision"] != fp.revision:
            # The buying message itself may change the need ("I'll take the dumper one"): re-evaluate first.
            rec = self.fit.recommend(fp, st["family_hint"])
            st["last_recommendation"] = rec
        ev = next((x for x in rec["evaluations"] if x["product_id"] == pid), None) or \
            self.fit.evaluate_product(self.store.get_product(pid), fp.known())
        if ev["status"] == EXCLUDED:
            cf = self.fit.counterfactual(ev, [x for x in rec["evaluations"] if x["status"] != EXCLUDED])
            reasons = "; ".join(f["reason"] for f in cf["failed_criteria"])
            alt = cf["current_alternative"]
            r.update(reply=f"**{ev['short_name']}** doesn't meet your stated requirements ({reasons}), so I won't "
                           f"prepare an order for it." + (f" **{alt['name']}** does fit." if alt else "") +
                           " If your requirement has changed, tell me and I'll recalculate.",
                     next_action="BLOCKED_HARD_CONSTRAINT", quick_replies=["Change a requirement"])
            return
        self._preview(sid, st, fp, r, self.store.get_product(pid), ev)

    def _cart_preview(self, sid: str, st: dict, r: dict, items: list[dict]) -> None:
        """One preview for several named products (each line from the catalogue price)."""
        decision = self.dealguard.evaluate(None)
        st["last_dealguard"] = decision
        prev = st["order_preview"]
        if prev and prev["status"] == "PENDING_CONFIRMATION":
            prev["status"] = "SUPERSEDED_BY_NEW_VERSION"
        preview = orders.build_cart_preview(items, self.store, decision, prev)
        st["order_preview"] = preview
        st["selected_product_id"] = preview["product_id"]
        st["lead_status"] = "ORDER_REVIEW"
        limitations, claims = [], []
        for it in preview["items"]:
            p = self.store.get_product(it["product_id"])
            claims.append(self.evidence_gate.resolve(p["product_id"], "price"))
            if p["family"] == "brush_cutter_trolley_attachment":
                limitations.append(f"{p['short_name']}: exact fitment with your machine is not confirmed by the catalogue.")
            for c in self.evidence_gate.summary_claims(p["product_id"]):
                if c["evidence_status"] in ("UNAPPROVED", "CONFLICTED") and c["claim_type"] in responses.CUSTOMER_CAUTIONS:
                    limitations.append(f"{p['short_name']}: {c['label'].lower()} is unconfirmed in the source.")
        preview["limitations"] = limitations
        self._set_receipt(st, claims, sid)
        db.log_event(sid, "ORDER_PREVIEW_CREATED", {"preview_id": preview["preview_id"], "version": preview["version"],
                                                    "lines": len(preview["items"])})
        preview["image"] = _image_view(self.store.get_product(preview["product_id"]))
        r["images"] = [i for i in (_image_view(self.store.get_product(x["product_id"])) for x in preview["items"][:3]) if i]
        r.update(reply="Here's your order with everything in one place:\n\n" + responses.order_preview(preview, limitations),
                 next_action="AWAIT_EXPLICIT_CONFIRMATION", quick_replies=["Confirm order", "Change an item", "Cancel"])
        r["side_effects"].append("order_preview_created")

    def _capture_contact(self, sid: str, st: dict, text: str, intent: str, r: dict) -> bool:
        """Name + town/district for the sales follow-up. Never traps the customer: anything else moves on."""
        if intent in ("OPT_OUT", "ASK_FOR_HUMAN", "CANCEL_OR_STOP", "CONFIRM_ORDER"):
            st["awaiting"] = None
            return False
        c = checkout.parse_contact(text)
        if c is None:
            st["awaiting"] = None
            return False
        contact = st.setdefault("contact", {})
        if c != "SKIP":
            if st["awaiting"] == "contact_area" and c.get("name") and not c.get("area"):
                c = {"name": None, "area": c["name"]}          # the reply to "which town?" is the town
            contact.update({k: v for k, v in c.items() if v})
            if st["awaiting"] == "contact" and contact.get("name") and not contact.get("area"):
                st["awaiting"] = "contact_area"
                r.update(reply=f"Thanks, {contact['name']}! And which town or district are you in? (Or reply **skip**.)",
                         next_action="ASK_CONTACT", quick_replies=["Skip"])
                return True
        st["awaiting"] = None
        order = db.update_order_details(sid, {"contact": contact or None,
                                              "contact_status": "CAPTURED" if contact else "SKIPPED"}) or st["order"]
        st["order"] = order
        db.log_event(sid, "ORDER_CONTACT", {"order_id": order["order_id"], "captured": bool(contact)})  # no PII in logs
        handoff.request_handoff(sid, st, "ORDER_FOLLOW_UP")
        r["side_effects"].append("handoff_queued")
        r.update(reply=checkout.summary(order, contact, st.get("channel", "browser")), next_action="ORDER_FINALISED",
                 quick_replies=[])
        return True

    def _preview(self, sid: str, st: dict, fp: NeedFingerprint, r: dict, product: dict, ev: dict | None = None) -> None:
        qty = fp.get("quantity") or 1
        decision = self.dealguard.evaluate(product)
        st["last_dealguard"] = decision
        price_claim = self.evidence_gate.resolve(product["product_id"], "price")
        prev = st["order_preview"] if st["order_preview"] and st["order_preview"]["product_id"] == product["product_id"] else None
        if prev and prev["status"] == "PENDING_CONFIRMATION":
            prev["status"] = "SUPERSEDED_BY_NEW_VERSION"
        preview = orders.build_preview(product, qty, self.store, price_claim, decision, prev)
        st["order_preview"] = preview
        st["selected_product_id"] = product["product_id"]
        st["lead_status"] = "ORDER_REVIEW"
        limitations = []
        if not fp.get("quantity"):
            limitations.append("Quantity assumed as 1 — tell me if you need more.")
        if product["family"] == "brush_cutter_trolley_attachment":
            m = fp.get("machine_model")
            limitations.append(f"Exact fitment with {m or 'your machine'} is not confirmed by the catalogue.")
        if ev and ev["status"] == UNVERIFIED:
            limitations += [c["reason"] for c in ev["checks"] if c["result"] != "PASS"]
        for c in self.evidence_gate.summary_claims(product["product_id"]):
            if c["evidence_status"] in ("UNAPPROVED", "CONFLICTED") and c["claim_type"] in responses.CUSTOMER_CAUTIONS:
                limitations.append(f"{c['label']} is unconfirmed in the source.")
        preview["limitations"] = limitations
        self._set_receipt(st, [price_claim], sid)
        db.log_event(sid, "ORDER_PREVIEW_CREATED", {"preview_id": preview["preview_id"], "version": preview["version"]})
        preview["image"] = _image_view(product)
        r.update(reply=responses.order_preview(preview, limitations), next_action="AWAIT_EXPLICIT_CONFIRMATION",
                 quick_replies=["Confirm order", "Change quantity", "Cancel"])
        r["side_effects"].append("order_preview_created")

    def _confirm(self, sid: str, st: dict, fp: NeedFingerprint, msg_id: str, r: dict) -> None:
        if st["order"]:
            db.log_event(sid, "DUPLICATE_CONFIRMATION_REJECTED", {"existing_order": st["order"]["order_id"]})
            r.update(reply=responses.duplicate_order(st["order"]), next_action="ORDER_ALREADY_EXISTS")
            return
        p = st["order_preview"]
        if not p or p["status"] != "PENDING_CONFIRMATION":
            note = ""
            if p and p["status"].startswith("SUPERSEDED"):
                note = "Your earlier preview no longer applies because your requirements changed. "
            r.update(reply=note + responses.nothing_to_confirm(), next_action="NO_PREVIEW_TO_CONFIRM",
                     quick_replies=["Show order preview"] if st["selected_product_id"] else [])
            return
        self.confirm_preview(sid, st.get("bound_preview_id") or p["preview_id"], msg_id, r)

    def confirm_preview(self, sid: str, preview_id: str, msg_id: str, r: dict | None = None) -> dict:
        """Confirmation bound to a specific preview version (used by chat and the API)."""
        st = self.state(sid)
        r = r if r is not None else {"side_effects": []}
        p = st["order_preview"]
        if st["order"]:
            r.update(reply=responses.duplicate_order(st["order"]), next_action="ORDER_ALREADY_EXISTS", ok=False)
            return r
        if not p or p["preview_id"] != preview_id or p["status"] != "PENDING_CONFIRMATION":
            r.update(reply="That preview is no longer current, so I can't confirm it. Ask me for a fresh preview.",
                     next_action="STALE_PREVIEW_REJECTED", ok=False)
            return r
        res = orders.confirm(sid, p, msg_id)
        p["status"] = "CONFIRMED"
        st["order"] = res["order"]
        st["lead_status"] = "ORDER_CONFIRMED"
        r.update(reply=responses.order_confirmed(res["order"]) if res["created"] else responses.duplicate_order(res["order"]),
                 next_action="ORDER_CONFIRMED" if res["created"] else "ORDER_ALREADY_EXISTS", ok=res["created"])
        if res["created"]:
            st["awaiting"] = "contact"
            r["reply"] += checkout.ask_contact(st.get("channel", "browser"))
            r["quick_replies"] = ["Skip"]
        r["side_effects"].append("mock_order_created" if res["created"] else "duplicate_rejected")
        return r

    # ── Helpers ──────────────────────────────────────────────

    def _set_receipt(self, st: dict, claims: list[dict], sid: str, rec: dict | None = None,
                     shown: list | None = None, excluded: list | None = None, prev_superseded: bool = False) -> dict:
        old = st["last_receipt"]
        receipt = self.evidence_gate.create_receipt(
            claims, conversation_id=sid, recommendation_id=rec["recommendation_id"] if rec else None,
            requirements_checked=[{"product_id": e["product_id"], "checks": e["checks"]} for e in (shown or [])],
            excluded_candidates=[{"product_id": e["product_id"], "name": e["short_name"],
                                  "counterfactual": e.get("counterfactual")} for e in (excluded or [])],
            limitations=[c.get("note") for c in claims if c["evidence_status"] != "SUPPORTED" and c.get("note")],
        )
        if old:
            receipt["supersedes"] = {"receipt_id": old["receipt_id"], "reason": "requirement changed" if prev_superseded else "new answer"}
            if prev_superseded:
                st.setdefault("superseded_receipts", []).append(old["receipt_id"])
        pids = list(dict.fromkeys(c["subject_product_id"] for c in claims))
        receipt["images"] = [i for i in (_image_view(self.store.get_product(pid)) for pid in pids
                                         if self.store.get_product(pid)) if i]
        st["last_receipt"] = receipt
        st["receipts"].append(receipt["receipt_id"])
        return receipt

    def _card(self, ev: dict, claims: list[dict]) -> dict:
        p = self.store.get_product(ev["product_id"])
        mine = [c for c in claims if c["subject_product_id"] == ev["product_id"]]
        return {"product_id": p["product_id"], "name": p["name"], "family": p["family_label"],
                "price": p["price"]["amount"], "status": ev["status"],
                "facts": [{"label": c["label"], "value": c["rendered_value"], "page": c["source_page"],
                           "qualifier": c.get("qualifier")} for c in mine if c["evidence_status"] == "SUPPORTED"
                          and c["claim_type"] != "price"],
                "limitations": [f"{c['label']}: not confirmed" for c in mine if c["evidence_status"] in
                                ("UNAPPROVED", "CONFLICTED") and c["claim_type"] in responses.CUSTOMER_CAUTIONS],
                "fit": [c["label"] for c in ev["checks"] if c["result"] == "PASS"],
                "image": _image_view(p),
                "image_page": (p["images"] or [{}])[0].get("page")}

    def _candidate_view(self, rec: dict | None) -> dict | None:
        if not rec:
            return None
        return {"recommendation_id": rec["recommendation_id"], "pool_size": rec["pool_size"],
                "eligible_count": len(rec["eligible"]), "unverified_count": len(rec["unverified"]),
                "excluded_count": len(rec["excluded"]), "catalogue_version": rec["catalogue_version"],
                "algorithm_version": rec["algorithm_version"], "fingerprint_revision": rec["fingerprint_revision"],
                "evaluations": rec["evaluations"]}

    def _summary(self, st: dict, fp: NeedFingerprint) -> str:
        known = fp.known()
        parts = [f"State: {st['lead_status']}"]
        if known:
            parts.append("Known: " + ", ".join(f"{k}={v}" for k, v in known.items()))
        if fp.unknown():
            parts.append("Open: " + ", ".join(fp.unknown()))
        if st["selected_product_id"]:
            parts.append(f"Focus: {st['selected_product_id']}")
        return " | ".join(parts)

    def _trace(self, sid, st, msg_id, text, intent, conf, nlu, entities, changes, rev_before, state_before,
               result, rec, t0) -> dict:
        receipt = st["last_receipt"]
        return {
            "trace_id": f"trace_{uuid.uuid4().hex[:10]}", "conversation_id": sid, "message_id": msg_id,
            "turn": st["turn"], "customer_text": text,
            "nlu": {"intent": intent, "confidence": conf, **nlu}, "entities": entities,
            "fingerprint_revision_before": rev_before, "fingerprint_revision_after": st["fingerprint"].revision,
            "changes": changes, "catalogue_version": self.store.version,
            "candidates": [{"product_id": e["product_id"], "status": e["status"],
                            "checks": {c["criterion"]: c["result"] for c in e["checks"]}}
                           for e in rec["evaluations"]] if rec else None,
            "asksmart": (result.get("question_scores") or [])[:4],
            "question_asked": (st["last_question"] or {}).get("slot") if result.get("next_action") == "ASK_CLARIFYING_QUESTION" else None,
            "receipt_id": receipt["receipt_id"] if receipt else None,
            "claims": [{"product": c["subject_product_id"], "field": c["claim_type"], "status": c["evidence_status"],
                        "page": c["source_page"]} for c in (receipt or {}).get("claims", [])][:20],
            "dealguard": st["last_dealguard"]["outcome"] if st["last_dealguard"] else None,
            "state_before": state_before, "state_after": st["lead_status"],
            "next_action": result.get("next_action"), "side_effects": result.get("side_effects", []),
            "bot_paused": st["bot_paused"], "processing_ms": round((time.perf_counter() - t0) * 1000, 1),
        }

    # ── Public reads / actions for the API ───────────────────

    def get_session_summary(self, sid: str) -> dict:
        st = self.state(sid)
        return {"session_id": sid, "lead_status": st["lead_status"], "fingerprint": st["fingerprint"].to_dict(),
                "turns": st["turn"], "summary": self._summary(st, st["fingerprint"]),
                "selected_product_id": st["selected_product_id"], "order": st["order"],
                "handoff": _handoff_view(st["handoff"]),
                "flags": {"bot_paused": st["bot_paused"], "opted_out": st["opted_out"],
                          "follow_up_pending": st["follow_up_pending"]}}

    def take_over(self, sid: str, agent: str) -> dict:
        st = self.state(sid)
        h = handoff.take_over(sid, st, agent)
        st["lead_status"] = "HUMAN_OWNED"
        return h

    def agent_message(self, sid: str, agent: str, text: str) -> dict:
        st = self.state(sid)
        if not st["bot_paused"]:
            raise PermissionError("Agent must take ownership before replying.")
        mid = f"msg_{uuid.uuid4().hex[:10]}"
        db.save_message(mid, sid, "human_agent", text)
        db.log_event(sid, "AGENT_MESSAGE", {"agent": agent, "message_id": mid})
        return {"message_id": mid, "role": "human_agent", "text": text}

    def resume_bot(self, sid: str, agent: str) -> dict | None:
        st = self.state(sid)
        h = handoff.resume_bot(sid, st, agent)
        st["lead_status"] = "RECOMMENDED" if st["selected_product_id"] else "DISCOVERY"
        return h


def _image_view(p: dict) -> dict | None:
    img = p.get("image")
    if not img:
        return None
    return {"product_id": p["product_id"], "url": img["url"], "page": img["page"], "sha256": img["sha256"],
            "source_file": img.get("source_file"), "caption": f"{p['short_name']} — catalogue p.{img['page']}"}


def _compact_scores(scored: list[dict]) -> list[dict]:
    return [{k: s[k] for k in ("slot", "question", "information_gain", "criticality", "coverage", "effort_cost",
                               "privacy_cost", "value", "blocking", "partitions", "candidate_count")} for s in scored]


def _handoff_view(h: dict | None) -> dict | None:
    if not h:
        return None
    return {k: h.get(k) for k in ("handoff_id", "reason", "reasons", "queue", "status", "assigned_agent",
                                  "created_at", "context_snapshot")}


def _json(obj) -> str:
    import json
    return json.dumps(obj, default=str)
