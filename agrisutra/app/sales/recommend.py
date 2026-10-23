"""Recommendation engine — hard-constraint gate + soft-preference ranking."""

from typing import Any
from app.catalogue.ingest import CatalogueStore
from app.dialogue.need_fingerprint import NeedFingerprint


class RecommendationEngine:
    """Applies hard constraints, then ranks survivors by soft preference match."""

    def __init__(self, store: CatalogueStore):
        self.store = store

    def get_candidates(self, fingerprint: NeedFingerprint) -> dict:
        """Run the full recommendation pipeline.

        Returns:
            {
                "all_products": [...],
                "after_hard_filter": [...],
                "ranked": [...],
                "eliminated": [...],
                "elimination_reasons": {product_id: [reasons]},
            }
        """
        known = fingerprint.get_known_fields()
        all_products = self.store.get_sprayer_products()
        eliminated = []
        elimination_reasons: dict[str, list[str]] = {}
        survivors = []

        for p in all_products:
            reasons = self._check_hard_constraints(p, known)
            if reasons:
                eliminated.append(p)
                elimination_reasons[p["product_id"]] = reasons
            else:
                survivors.append(p)

        # Rank survivors by soft preferences
        ranked = self._rank_by_preferences(survivors, known)

        return {
            "all_products": all_products,
            "after_hard_filter": survivors,
            "ranked": ranked,
            "eliminated": eliminated,
            "elimination_reasons": elimination_reasons,
        }

    def _check_hard_constraints(self, product: dict, known: dict) -> list[str]:
        """Check hard constraints. Returns list of failure reasons (empty = pass)."""
        reasons = []

        # Budget constraint
        budget = known.get("budget")
        price = product.get("price", {}).get("amount")
        if budget is not None and price is not None:
            if price > budget:
                reasons.append(f"Price ₹{price} exceeds budget ₹{budget}")

        # Farm size constraint
        farm_size = known.get("farm_size_acres")
        if farm_size is not None:
            apps_text = " ".join(product.get("applications", [])).lower()
            lim_text = " ".join(product.get("limitations", [])).lower()
            # Check if product explicitly says not suitable
            if farm_size > 5 and "not suitable for large farms" in lim_text:
                reasons.append(f"Not suitable for farms over 5 acres (your farm: {farm_size} acres)")
            if farm_size > 5 and "not suitable for farm-scale" in lim_text:
                reasons.append(f"Not suitable for farm-scale use (your farm: {farm_size} acres)")
            if farm_size <= 1 and "garden" not in apps_text and "home" not in apps_text:
                # Small plots — engine sprayers may be overkill, but not a hard fail
                pass

        # Category / type constraint
        pref_type = known.get("preferred_type")
        if pref_type is not None and product.get("category") != pref_type:
            reasons.append(f"Category '{product['category']}' does not match preferred '{pref_type}'")

        return reasons

    def _rank_by_preferences(self, products: list[dict], known: dict) -> list[dict]:
        """Rank surviving products by soft preference match."""
        scored = []
        for p in products:
            score, match_details = self._compute_fit_score(p, known)
            scored.append({
                **p,
                "fit_score": score,
                "match_details": match_details,
            })
        scored.sort(key=lambda x: x["fit_score"], reverse=True)
        return scored

    def _compute_fit_score(self, product: dict, known: dict) -> tuple[float, list[str]]:
        """Compute an explainable fit score. Returns (score, [match reasons])."""
        total_weight = 0.0
        matched_weight = 0.0
        details = []

        # Budget fit (closer to budget = better value)
        budget = known.get("budget")
        price = product.get("price", {}).get("amount")
        if budget is not None and price is not None:
            total_weight += 3.0
            ratio = price / budget
            if ratio <= 1.0:
                budget_score = 1.0 - (1.0 - ratio) * 0.5  # Prefer closer to budget
                matched_weight += 3.0 * budget_score
                details.append(f"Price ₹{price} is within budget ₹{budget} ({int(ratio*100)}% of budget)")
        elif budget is not None:
            total_weight += 3.0
            details.append("Price not available in catalogue — cannot verify budget fit")

        # Application match
        intended_use = known.get("intended_use")
        if intended_use is not None:
            total_weight += 2.0
            apps = [a.lower() for a in product.get("applications", [])]
            if any(intended_use.lower() in a for a in apps):
                matched_weight += 2.0
                details.append(f"Supports intended use: {intended_use}")
            else:
                details.append(f"Intended use '{intended_use}' not listed in applications")

        # Farm size suitability
        farm_size = known.get("farm_size_acres")
        if farm_size is not None:
            total_weight += 2.0
            apps_text = " ".join(product.get("applications", [])).lower()
            if farm_size <= 2 and ("garden" in apps_text or "small" in apps_text):
                matched_weight += 2.0
                details.append(f"Suitable for small area ({farm_size} acres)")
            elif farm_size <= 5 and "5 acres" in apps_text:
                matched_weight += 2.0
                details.append(f"Suitable for up to 5 acres")
            elif farm_size <= 10 and "10 acres" in apps_text:
                matched_weight += 2.0
                details.append(f"Suitable for up to 10 acres")
            elif farm_size <= 25 and "25 acres" in apps_text:
                matched_weight += 2.0
                details.append(f"Suitable for up to 25 acres")
            elif farm_size <= 8 and "8 acres" in apps_text:
                matched_weight += 2.0
                details.append(f"Suitable for up to 8 acres")
            else:
                details.append(f"Farm size fit not confirmed from catalogue for {farm_size} acres")

        # Availability bonus
        total_weight += 1.0
        if product.get("availability") == "IN_STOCK":
            matched_weight += 1.0
            details.append("In stock")
        elif product.get("availability") == "LIMITED_STOCK":
            matched_weight += 0.5
            details.append("Limited stock")
        else:
            details.append("Availability unknown")

        score = round(matched_weight / total_weight, 2) if total_weight > 0 else 0.5
        return score, details
