"""DealGuard (M5) — approved price, discount and quote rules.

The supplied files contain list prices only, so the default outcome for any
concession is NO_POLICY: the bot states the listed price and routes the request
to a salesperson. An absent limit is never read as permission.
"""

import json
import os
from datetime import datetime, timezone

DEFAULT_POLICY_PATH = "./data/policy/price_policy.json"

ALLOW, DENY, REQUIRES_APPROVAL, EXPIRED, NO_POLICY = "ALLOW", "DENY", "REQUIRES_APPROVAL", "EXPIRED", "NO_POLICY"


class DealGuard:
    def __init__(self, policy_path: str | None = None):
        self.path = policy_path or os.environ.get("PRICE_POLICY_PATH", DEFAULT_POLICY_PATH)
        self.policy_set: dict = {"status": "NO_APPROVED_POLICY", "policies": []}
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as f:
                self.policy_set = json.load(f)

    def _policy_for(self, product_id: str) -> dict | None:
        return next((p for p in self.policy_set.get("policies", []) if p.get("product_id") == product_id), None)

    def evaluate(self, product: dict | None, requested_discount_percent: float | None = None,
                 requested_price: float | None = None) -> dict:
        now = datetime.now(timezone.utc)
        base = {
            "product_id": product["product_id"] if product else None,
            "list_price": product["price"].get("amount") if product else None,
            "requested_discount_percent": requested_discount_percent,
            "requested_price": requested_price,
            "policy_id": None, "policy_version": None, "evaluated_at": now.isoformat(),
        }
        if not product:
            return {**base, "outcome": NO_POLICY, "reason": "No product selected; nothing can be quoted."}
        policy = self._policy_for(product["product_id"])
        if not policy:
            return {**base, "outcome": NO_POLICY,
                    "reason": "No approved discount or price policy exists for this product in the supplied files. "
                              "Only the catalogue-listed price may be stated; a salesperson must handle any concession."}
        base.update(policy_id=policy.get("policy_id"), policy_version=policy.get("version"))
        start, end = policy.get("effective_from"), policy.get("effective_to")
        if (start and now < datetime.fromisoformat(start)) or (end and now > datetime.fromisoformat(end)):
            return {**base, "outcome": EXPIRED, "reason": "Price policy is outside its validity window."}
        list_price = policy.get("list_price") or base["list_price"]
        pct = requested_discount_percent
        if pct is None and requested_price is not None and list_price:
            pct = round((1 - requested_price / list_price) * 100, 2)
        if pct is None or pct <= 0:
            return {**base, "outcome": ALLOW, "reason": "List price within policy."}
        auto = policy.get("auto_approval_limit") or 0
        max_pct = policy.get("maximum_discount_percent") or 0
        if pct <= auto:
            return {**base, "outcome": ALLOW, "reason": f"{pct}% is within the auto-approval limit of {auto}%."}
        if pct <= max_pct:
            return {**base, "outcome": REQUIRES_APPROVAL,
                    "reason": f"{pct}% needs approval by {policy.get('approval_role', 'a sales manager')}."}
        return {**base, "outcome": DENY, "reason": f"{pct}% exceeds the maximum approved discount of {max_pct}%."}
