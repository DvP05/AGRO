"""Truth Receipt and evidence validation for catalogue-grounded claims."""

import uuid
from typing import Any

from app.catalogue.ingest import CatalogueStore


class EvidenceStatus:
    VERIFIED = "VERIFIED"
    MISSING = "MISSING"
    CONFLICT = "CONFLICT"
    UNKNOWN = "UNKNOWN"


class EvidenceGate:
    """Creates and validates Truth Receipts linking product claims to catalogue fields."""

    def __init__(self, store: CatalogueStore):
        self.store = store

    def create_receipt(self, product_id: str, claims: list[dict]) -> dict:
        """Create a Truth Receipt for a set of claims about a product.
        
        Each claim: {"claim_type": "price", "field_path": "price.amount", "stated_value": 1850}
        """
        product = self.store.get_product(product_id)
        if product is None:
            return {
                "receipt_id": f"TR-{uuid.uuid4().hex[:8].upper()}",
                "product_id": product_id,
                "status": "PRODUCT_NOT_FOUND",
                "claims": [],
            }

        verified_claims = []
        overall_status = EvidenceStatus.VERIFIED

        for claim in claims:
            source_value = self.store.get_field_value(product_id, claim["field_path"])
            if source_value is None:
                status = EvidenceStatus.MISSING
                overall_status = EvidenceStatus.MISSING
            elif str(source_value) == str(claim.get("stated_value")):
                status = EvidenceStatus.VERIFIED
            else:
                status = EvidenceStatus.CONFLICT
                overall_status = EvidenceStatus.CONFLICT

            verified_claims.append({
                "claim_id": f"CL-{uuid.uuid4().hex[:6].upper()}",
                "claim_type": claim.get("claim_type", "fact"),
                "source_field": claim["field_path"],
                "source_value": source_value,
                "stated_value": claim.get("stated_value"),
                "evidence_status": status,
                "status": status,
            })

        return {
            "receipt_id": f"TR-{uuid.uuid4().hex[:8].upper()}",
            "product_id": product_id,
            "product_name": product["name"],
            "catalogue_version": self.store.version,
            "overall_status": overall_status,
            "status": overall_status,
            "claims": verified_claims,
        }

    def verify_claim(self, product_id: str, field_path: str, stated_value: Any) -> dict:
        """Verify a single claim against the catalogue."""
        receipt = self.create_receipt(product_id, [
            {"claim_type": "fact", "field_path": field_path, "stated_value": stated_value}
        ])
        return receipt["claims"][0] if receipt["claims"] else {"evidence_status": EvidenceStatus.UNKNOWN}

    def build_product_evidence(self, product_id: str) -> dict:
        """Build a complete evidence summary for a product recommendation."""
        product = self.store.get_product(product_id)
        if not product:
            return {"product_id": product_id, "status": "NOT_FOUND", "facts": []}

        facts = []
        # Price evidence
        price = product.get("price", {})
        if price.get("amount") is not None:
            facts.append({
                "field": "price.amount",
                "value": f"₹{price['amount']}",
                "status": EvidenceStatus.VERIFIED,
                "source": product["source_ref"],
            })
        else:
            facts.append({
                "field": "price.amount",
                "value": None,
                "status": EvidenceStatus.MISSING,
                "source": product["source_ref"],
            })

        # Key specs evidence
        specs = product.get("specifications", {})
        for key, val in specs.items():
            if isinstance(val, dict):
                display = f"{val.get('value', '?')} {val.get('unit', '')}".strip()
            elif isinstance(val, bool):
                display = "Yes" if val else "No"
            else:
                display = str(val)
            facts.append({
                "field": f"specifications.{key}",
                "value": display,
                "status": EvidenceStatus.VERIFIED,
                "source": product["source_ref"],
            })

        # Limitations
        for lim in product.get("limitations", []):
            facts.append({
                "field": "limitations",
                "value": lim,
                "status": EvidenceStatus.VERIFIED,
                "source": product["source_ref"],
            })

        return {
            "product_id": product_id,
            "product_name": product["name"],
            "catalogue_version": self.store.version,
            "facts": facts,
        }
