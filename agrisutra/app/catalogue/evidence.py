"""Truth Receipt / Proofline (M4).

Every product claim the agent wants to make is resolved here against the
catalogue. Only SUPPORTED claims may be stated to the customer; everything else
must be disclosed as missing, unapproved or conflicting.
"""

import uuid
from datetime import datetime, timezone

from app.catalogue.ingest import CatalogueStore


class EvidenceStatus:
    SUPPORTED = "SUPPORTED"
    MISSING = "MISSING"
    CONFLICTED = "CONFLICTED"
    STALE = "STALE"
    UNAPPROVED = "UNAPPROVED"
    DERIVED = "DERIVED"


# Customer-facing topic → catalogue field. Fields not in a product record are MISSING.
TOPIC_LABELS = {
    "price": "Listed price",
    "engine_warranty": "Engine warranty",
    "warranty": "Warranty",
    "engine_displacement": "Engine displacement",
    "power": "Power",
    "run_time": "Running time",
    "construction": "Construction",
    "carburetor": "Carburetor",
    "engine_type": "Engine type",
    "model": "Model",
    "configuration": "Configuration",
    "wheel_design": "Wheel design",
    "tipping_mechanism": "Tipping mechanism",
    "described_uses": "Described uses",
    "operation": "Operation",
    "product_type": "Product type",
    "capacity": "Capacity",
    "load_capacity": "Load capacity",
    "exact_fitment": "Exact machine fitment",
    "stock": "Stock / availability",
    "delivery": "Delivery time / charges",
    "dimensions": "Dimensions",
    "weight": "Weight",
    "gst": "GST / tax treatment",
    "payment": "Payment options",
    "discount": "Discount",
    "fuel_tank": "Fuel tank",
}

# Which facts summarise a product in a recommendation, per family.
SUMMARY_FIELDS = {
    "brush_cutter_trolley_attachment": ["price", "configuration", "construction", "wheel_design", "operation"],
    "wheel_barrow": ["price", "tipping_mechanism", "construction", "wheel_design", "described_uses"],
    "brush_cutter_machine": ["price", "product_type", "engine_type", "power", "carburetor", "engine_warranty", "run_time"],
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EvidenceGate:
    """Maps claims to source fields and blocks anything that is not SUPPORTED."""

    def __init__(self, store: CatalogueStore):
        self.store = store

    # ── Claim resolution ─────────────────────────────────────

    def resolve(self, product_id: str, field: str) -> dict:
        product = self.store.get_product(product_id)
        base = {
            "claim_id": f"claim_{uuid.uuid4().hex[:8]}",
            "subject_product_id": product_id,
            "subject_name": product["short_name"] if product else product_id,
            "claim_type": field,
            "label": TOPIC_LABELS.get(field, field.replace("_", " ").title()),
            "rendered_value": None,
            "observed_value": None,
            "qualifier": None,
            "note": None,
            "source_field": None,
            "source_file": self.store.source_file,
            "source_page": None,
            "source_location": None,
            "source_hash": self.store.source_hash,
            "catalogue_version": self.store.version,
        }
        if not product:
            return {**base, "evidence_status": EvidenceStatus.MISSING, "approval_status": None,
                    "note": "Product is not in the catalogue."}

        if field == "price":
            price = product["price"]
            if price.get("amount") is None:
                return {**base, "evidence_status": EvidenceStatus.MISSING, "source_field": "price.amount"}
            return {
                **base,
                "rendered_value": f"₹{price['amount']:,}",
                "qualifier": "catalogue-listed price; no effective date or tax treatment in the source",
                "source_field": "price.amount",
                "source_page": price["source"]["page"],
                "source_location": price["source"]["location"],
                "evidence_status": EvidenceStatus.SUPPORTED,
                "approval_status": price.get("approval_status"),
            }

        if field == "stock":
            return {**base, "evidence_status": EvidenceStatus.MISSING, "source_field": "availability.status",
                    "note": "No stock source is connected; availability is UNKNOWN."}

        if field == "warranty" and "engine_warranty" in product["facts"]:
            field = "engine_warranty"
            base["claim_type"] = field
            base["label"] = TOPIC_LABELS[field]

        fact = product["facts"].get(field)
        if fact:
            status = {
                "APPROVED": EvidenceStatus.SUPPORTED,
                "UNAPPROVED": EvidenceStatus.UNAPPROVED,
                "CONFLICTED": EvidenceStatus.CONFLICTED,
            }.get(fact.get("status"), EvidenceStatus.UNAPPROVED)
            return {
                **base,
                "label": fact.get("label", base["label"]),
                "rendered_value": fact["value"] if status == EvidenceStatus.SUPPORTED else None,
                "observed_value": fact["value"],
                "qualifier": fact.get("qualifier"),
                "note": fact.get("note"),
                "source_field": f"facts.{field}.value",
                "source_page": fact["source"]["page"],
                "source_location": fact["source"]["location"],
                "evidence_status": status,
                "approval_status": fact.get("status"),
            }

        obs = self.store.get_observation(product_id, field)
        if obs and obs.get("qualitative_only"):
            # A marketing tag with no figure ("universal", "high load capacity") leaves the fact MISSING.
            return {**base, "evidence_status": EvidenceStatus.MISSING, "observed_value": obs["value"],
                    "source_field": f"observations.{field}", "source_page": obs["source"]["page"],
                    "source_location": obs["source"]["location"], "approval_status": obs["status"],
                    "note": obs.get("note")}
        if obs:
            status = (EvidenceStatus.CONFLICTED if "CONFLICT" in obs["status"]
                      else EvidenceStatus.UNAPPROVED)
            return {
                **base,
                "observed_value": obs["value"],
                "note": obs.get("note"),
                "source_field": f"observations.{field}",
                "source_page": obs["source"]["page"],
                "source_location": obs["source"]["location"],
                "evidence_status": status,
                "approval_status": obs["status"],
            }

        return {**base, "evidence_status": EvidenceStatus.MISSING,
                "note": "The supplied catalogue does not state this."}

    @staticmethod
    def statable(claim: dict) -> bool:
        return claim.get("evidence_status") == EvidenceStatus.SUPPORTED

    # ── Receipts ─────────────────────────────────────────────

    def create_receipt(self, claims: list[dict], conversation_id: str | None = None,
                       recommendation_id: str | None = None,
                       requirements_checked: list | None = None,
                       excluded_candidates: list | None = None,
                       limitations: list | None = None) -> dict:
        return {
            "receipt_id": f"tr_{uuid.uuid4().hex[:10]}",
            "conversation_id": conversation_id,
            "recommendation_id": recommendation_id,
            "catalogue_version": self.store.version,
            "source_file": self.store.source_file,
            "source_hash": self.store.source_hash,
            "created_at": _now(),
            "claims": claims,
            "requirements_checked": requirements_checked or [],
            "limitations": limitations or [],
            "excluded_candidates": excluded_candidates or [],
            "superseded_by": None,
        }

    def summary_claims(self, product_id: str) -> list[dict]:
        product = self.store.get_product(product_id)
        if not product:
            return []
        claims = [self.resolve(product_id, f) for f in SUMMARY_FIELDS.get(product["family"], ["price"])]
        # Disclose open source flags for this product as part of its receipt.
        for obs in product["observations"]:
            claims.append(self.resolve(product_id, obs["field"]))
        return claims

    def build_product_evidence(self, product_id: str) -> dict:
        """Full evidence view for one product (cockpit / API)."""
        product = self.store.get_product(product_id)
        if not product:
            return {"product_id": product_id, "status": "NOT_FOUND", "claims": []}
        claims = [self.resolve(product_id, "price")]
        claims += [self.resolve(product_id, f) for f in product["facts"]]
        claims += [self.resolve(product_id, o["field"]) for o in product["observations"]]
        claims += [self.resolve(product_id, f) for f in product["not_stated"]]
        receipt = self.create_receipt(claims)
        receipt["product_id"] = product_id
        receipt["product_name"] = product["name"]
        img = product.get("image")
        receipt["images"] = [{"product_id": product_id, "url": img["url"], "page": img["page"],
                              "sha256": img["sha256"], "source_file": img.get("source_file"),
                              "caption": f"{product['short_name']} — catalogue p.{img['page']}"}] if img else []
        return receipt
