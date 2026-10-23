"""Dialogue Manager — state machine orchestrating the entire conversation turn."""

import uuid
import json
from typing import Any

from app.nlu.predict import classify_intent, extract_entities
from app.dialogue.need_fingerprint import NeedFingerprint
from app.dialogue.question_value import QuestionValueEngine
from app.catalogue.ingest import CatalogueStore
from app.catalogue.retrieval import CatalogueRetriever
from app.catalogue.evidence import EvidenceGate
from app.sales.recommend import RecommendationEngine
from app.sales import responses, orders
from app.storage import database as db


# ── Lead statuses ─────────────────────────────────────────────
STATUSES = ["NEW", "DISCOVERY", "QUALIFIED", "RECOMMENDED",
            "HUMAN_REVIEW", "ORDER_REVIEW", "ORDER_CREATED", "CLOSED_NO_ORDER"]


class DialogueManager:
    """Stateful per-session dialogue manager."""

    def __init__(self, store: CatalogueStore):
        self.store = store
        self.retriever = CatalogueRetriever(store)
        self.evidence_gate = EvidenceGate(store)
        self.recommender = RecommendationEngine(store)
        self.question_engine = QuestionValueEngine(store)
        self.sessions: dict[str, dict] = {}  # in-memory session state

    def _get_session_state(self, session_id: str) -> dict:
        if session_id not in self.sessions:
            self.sessions[session_id] = {
                "fingerprint": NeedFingerprint(session_id),
                "lead_status": "NEW",
                "last_recommendation": None,
                "selected_product": None,
                "pending_order_preview": False,
                "message_count": 0,
            }
        return self.sessions[session_id]

    def process_message(self, session_id: str, user_text: str) -> dict:
        """Process a customer message and return the full response payload."""
        state = self._get_session_state(session_id)
        fp: NeedFingerprint = state["fingerprint"]
        msg_id = f"msg-{uuid.uuid4().hex[:8]}"
        state["message_count"] += 1

        # 1. Classify intent
        intent, confidence = classify_intent(user_text)

        # 2. Extract entities
        product_names = self.store.get_product_names()
        entities = extract_entities(user_text, product_names)

        # 3. Update fingerprint
        changes = fp.update_from_entities(entities, msg_id)

        # 4. Save user message
        db.save_message(msg_id, session_id, "user", user_text, intent, confidence)

        # 5. Log event
        db.log_event(session_id, "TURN_PROCESSED", {
            "message_id": msg_id,
            "intent": intent,
            "confidence": confidence,
            "entities": entities,
            "changes": changes,
        })

        # 6. Decide response based on intent + state
        response_data = self._decide_response(
            session_id, state, fp, intent, confidence, entities, changes, user_text, msg_id
        )

        # 7. Save agent response
        reply_id = f"msg-{uuid.uuid4().hex[:8]}"
        db.save_message(reply_id, session_id, "agent", response_data["reply"], intent, confidence)

        # 8. Update session in DB
        db.update_session(
            session_id,
            lead_status=state["lead_status"],
            need_fingerprint=json.dumps(fp.to_dict()),
            summary=self._build_summary(state, fp),
        )

        return {
            "message_id": msg_id,
            "reply_id": reply_id,
            "reply": response_data["reply"],
            "intent": intent,
            "confidence": confidence,
            "entities": entities,
            "fingerprint": fp.to_dict(),
            "lead_status": state["lead_status"],
            "changes": changes,
            "candidates": response_data.get("candidates"),
            "evidence": response_data.get("evidence"),
            "question_scores": response_data.get("question_scores"),
            "order": response_data.get("order"),
        }

    def _decide_response(self, session_id: str, state: dict,
                         fp: NeedFingerprint, intent: str, confidence: float,
                         entities: dict, changes: list, user_text: str,
                         msg_id: str) -> dict:
        """Central decision logic — routes to the right response strategy."""
        result: dict[str, Any] = {}

        # ── Greeting ──────────────────────────────────────────
        if intent == "GREETING" and state["message_count"] <= 2:
            state["lead_status"] = "NEW"
            result["reply"] = responses.greeting_response()
            return result

        # ── Cancel / Stop ─────────────────────────────────────
        if intent == "CANCEL_OR_STOP":
            state["lead_status"] = "CLOSED_NO_ORDER"
            state["pending_order_preview"] = False
            state["selected_product"] = None
            result["reply"] = responses.cancel_response()
            return result

        # ── Ask for human ─────────────────────────────────────
        if intent == "ASK_FOR_HUMAN":
            state["lead_status"] = "HUMAN_REVIEW"
            result["reply"] = responses.escalation_response()
            return result

        # ── Order confirmation (explicit consent check) ───────
        if intent == "CONFIRM_ORDER" and state["pending_order_preview"] and state["selected_product"]:
            product = state["selected_product"]
            quantity = fp.get_known_fields().get("quantity", 1)
            order_result = orders.create_order(
                session_id=session_id,
                product_id=product["product_id"],
                product_name=product["name"],
                quantity=quantity,
                store=self.store,
                confirmation_message_id=msg_id,
            )
            if order_result.get("success"):
                state["lead_status"] = "ORDER_CREATED"
                state["pending_order_preview"] = False
                result["reply"] = responses.order_confirmed_response(
                    order_result["order_id"], product["name"], quantity
                )
                result["order"] = order_result
            else:
                result["reply"] = f"⚠️ {order_result.get('error', 'Could not create order.')}. "
                if order_result.get("existing_order"):
                    result["reply"] += f"You already have order `{order_result['existing_order']['order_id']}`."
                result["order"] = order_result
            return result

        # ── Buying intent → show order preview ────────────────
        if intent == "EXPRESS_BUYING_INTENT" or (
            intent == "CONFIRM_ORDER" and not state["pending_order_preview"]
        ):
            rec = state.get("last_recommendation")
            if rec and rec.get("ranked"):
                product = rec["ranked"][0]
                # Check for mentioned product
                mentioned = entities.get("mentioned_product")
                if mentioned:
                    for p in rec["ranked"]:
                        if mentioned.lower() in p["name"].lower():
                            product = p
                            break
                state["selected_product"] = product
                state["pending_order_preview"] = True
                state["lead_status"] = "ORDER_REVIEW"
                quantity = fp.get_known_fields().get("quantity", 1)
                result["reply"] = responses.order_preview_response(product, quantity)
                result["evidence"] = self.evidence_gate.build_product_evidence(product["product_id"])
            else:
                # No recommendation yet — need discovery first
                result["reply"] = (
                    "I'd love to help you place an order! But first, "
                    "let me make sure I recommend the right product for your needs.\n\n"
                )
                result["reply"] += self._do_discovery(state, fp, result)
            return result

        # ── Correction ────────────────────────────────────────
        if intent == "CORRECT_REQUIREMENT" or (changes and any(c.get("type") == "correction" for c in changes)):
            state["pending_order_preview"] = False
            state["selected_product"] = None
            reply = responses.correction_response(changes, fp.get_known_fields())
            # Re-run recommendation
            rec_result = self.recommender.get_candidates(fp)
            state["last_recommendation"] = rec_result
            if rec_result["ranked"]:
                state["lead_status"] = "RECOMMENDED"
                reply += "\n\n" + responses.recommendation_response(
                    rec_result["ranked"], len(rec_result["eliminated"])
                )
                result["candidates"] = self._summarise_candidates(rec_result)
                if rec_result["ranked"]:
                    result["evidence"] = self.evidence_gate.build_product_evidence(
                        rec_result["ranked"][0]["product_id"]
                    )
            else:
                state["lead_status"] = "DISCOVERY"
            result["reply"] = reply
            return result

        # ── Product fact question ─────────────────────────────
        if intent == "ASK_PRODUCT_FACT":
            user_lower = user_text.lower()
            # Anti-hallucination guard for ungrounded extreme claims / warranties
            if any(term in user_lower for term in ["10-year", "10 year", "ten year", "lifetime", "free replacement", "unlimited warranty"]):
                result["reply"] = (
                    "The catalogue does not state a 10-year or lifetime free replacement warranty. "
                    "Our products carry between 6 months and 2 years standard manufacturer warranty as listed in the catalogue. "
                    "I have flagged this requirement for staff review if you need special warranty terms."
                )
                result["evidence"] = {"claims": [], "status": "UNKNOWN"}
                return result

            # Try to find specific FAQ
            faq_results = self.retriever.search_faq(user_text, top_k=1)
            if faq_results and faq_results[0].get("score", 0) > 0.15:
                faq = faq_results[0]
                product = self.store.get_product(faq["product_id"])
                if product:
                    result["reply"] = f"**{product['name']}:** {faq.get('answer', 'No FAQ answer available.')}"
                    result["evidence"] = self.evidence_gate.build_product_evidence(faq["product_id"])
                    return result

            # Try product search
            product_match = self.retriever.get_product_for_query(user_text)
            if product_match:
                result["reply"] = responses.product_fact_response(product_match, user_text)
                result["evidence"] = self.evidence_gate.build_product_evidence(product_match["product_id"])
                return result

            result["reply"] = responses.unknown_fact_response(user_text)
            return result

        # ── Compare products ──────────────────────────────────
        if intent == "COMPARE_PRODUCTS":
            rec = state.get("last_recommendation")
            if rec and len(rec.get("ranked", [])) >= 2:
                result["reply"] = responses.comparison_response(rec["ranked"][:3])
                result["candidates"] = self._summarise_candidates(rec)
            else:
                # Compare all sprayers
                all_sprayers = self.store.get_sprayer_products()
                result["reply"] = responses.comparison_response(all_sprayers[:3])
            return result

        # ── Objection handling ────────────────────────────────
        if intent == "RAISE_OBJECTION":
            rec = state.get("last_recommendation")
            if rec and rec.get("ranked"):
                # Show cheaper alternatives
                reply = "I understand your concern. Let me check for alternatives.\n\n"
                result["reply"] = reply + self._do_discovery(state, fp, result)
            else:
                result["reply"] = (
                    "I understand your concern. Could you tell me your budget "
                    "so I can find options that fit? We have products starting from ₹950."
                )
            return result

        # ── Discovery / Requirement / general ─────────────────
        if changes:
            state["lead_status"] = "DISCOVERY"

        # Check if we have enough info to recommend
        completion = fp.get_completion_ratio()
        if completion >= 0.4 or intent == "DISCOVER_PRODUCT":
            result["reply"] = self._do_discovery(state, fp, result)
        elif intent == "STATE_REQUIREMENT" and changes:
            state["lead_status"] = "DISCOVERY"
            reply = "Got it! I've noted your requirements.\n\n"
            result["reply"] = reply + self._do_discovery(state, fp, result)
        else:
            # Not enough context — ask a question
            candidates = self.store.get_sprayer_products()
            next_q = self.question_engine.select_next_question(fp, candidates)
            all_q = self.question_engine.get_all_scored_questions(fp, candidates)
            result["question_scores"] = all_q
            if next_q:
                state["lead_status"] = "DISCOVERY"
                result["reply"] = responses.ask_question_response(
                    next_q["question"], fp.get_known_fields()
                )
            else:
                result["reply"] = responses.fallback_response()

        return result

    def _do_discovery(self, state: dict, fp: NeedFingerprint, result: dict) -> str:
        """Run recommendation and/or ask next question."""
        rec_result = self.recommender.get_candidates(fp)
        state["last_recommendation"] = rec_result
        result["candidates"] = self._summarise_candidates(rec_result)

        if rec_result["ranked"]:
            state["lead_status"] = "RECOMMENDED"
            reply = responses.recommendation_response(
                rec_result["ranked"], len(rec_result["eliminated"])
            )
            result["evidence"] = self.evidence_gate.build_product_evidence(
                rec_result["ranked"][0]["product_id"]
            )

            # If there are still unknowns, also ask a question
            next_q = self.question_engine.select_next_question(fp, rec_result["ranked"])
            all_q = self.question_engine.get_all_scored_questions(fp, rec_result["ranked"])
            result["question_scores"] = all_q
            if next_q and next_q["value_score"] > 0.3:
                reply += f"\n\n💡 *To refine further:* {next_q['question']}"
            return reply
        else:
            state["lead_status"] = "DISCOVERY"
            candidates = self.store.get_sprayer_products()
            next_q = self.question_engine.select_next_question(fp, candidates)
            all_q = self.question_engine.get_all_scored_questions(fp, candidates)
            result["question_scores"] = all_q
            if next_q:
                return responses.ask_question_response(
                    next_q["question"], fp.get_known_fields()
                )
            return responses.fallback_response()

    def _summarise_candidates(self, rec: dict) -> dict:
        """Build a compact candidate summary for the UI panel."""
        return {
            "total": len(rec["all_products"]),
            "after_filter": len(rec["after_hard_filter"]),
            "ranked_count": len(rec["ranked"]),
            "eliminated_count": len(rec["eliminated"]),
            "top_products": [
                {
                    "product_id": p["product_id"],
                    "name": p["name"],
                    "category": p.get("category"),
                    "price": p.get("price", {}).get("amount"),
                    "fit_score": p.get("fit_score", 0),
                    "match_details": p.get("match_details", []),
                }
                for p in rec["ranked"][:5]
            ],
            "eliminated_products": [
                {
                    "product_id": p["product_id"],
                    "name": p["name"],
                    "reasons": rec["elimination_reasons"].get(p["product_id"], []),
                }
                for p in rec["eliminated"]
            ],
        }

    def _build_summary(self, state: dict, fp: NeedFingerprint) -> str:
        known = fp.get_known_fields()
        unknown = fp.get_unknown_fields()
        parts = [f"Status: {state['lead_status']}"]
        if known:
            parts.append("Known: " + ", ".join(f"{k}={v}" for k, v in known.items()))
        if unknown:
            parts.append("Unknown: " + ", ".join(unknown))
        return " | ".join(parts)

    def get_session_summary(self, session_id: str) -> dict:
        state = self._get_session_state(session_id)
        fp: NeedFingerprint = state["fingerprint"]
        return {
            "session_id": session_id,
            "lead_status": state["lead_status"],
            "fingerprint": fp.to_dict(),
            "message_count": state["message_count"],
            "has_order": state["lead_status"] == "ORDER_CREATED",
            "summary": self._build_summary(state, fp),
        }
