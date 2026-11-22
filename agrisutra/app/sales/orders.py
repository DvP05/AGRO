"""Order preview and consent-bound mock order creation (M5).

A preview is immutable: any change (quantity, product, requirement) creates a
new preview version. Confirmation must reference the current preview, and the
idempotency key makes repeated confirmations harmless.
"""

import uuid
from datetime import datetime, timezone

from app.catalogue.ingest import CatalogueStore
from app.storage import database as db


def build_preview(product: dict, quantity: int, store: CatalogueStore, price_claim: dict,
                  dealguard_decision: dict, previous: dict | None = None) -> dict:
    version = (previous["version"] + 1) if previous else 1
    unit_price = product["price"].get("amount")
    return {
        "preview_id": f"prev_{uuid.uuid4().hex[:10]}",
        "version": version,
        "order_type": "MOCK",
        "product_id": product["product_id"],
        "product_name": product["name"],
        "quantity": quantity,
        "unit_price": unit_price,
        "line_total": unit_price * quantity if unit_price is not None else None,
        "currency": product["price"].get("currency", "INR"),
        "price_status": product["price"].get("approval_status"),
        "price_claim_id": price_claim.get("claim_id"),
        "dealguard_outcome": dealguard_decision.get("outcome"),
        "catalogue_version": store.version,
        "terms_unknown": ["GST / tax treatment", "delivery charges and timeline", "stock availability", "payment method"],
        "status": "PENDING_CONFIRMATION",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "label": "SIMULATION — no payment is taken and no real order is placed",
    }


def build_cart_preview(items: list[dict], store: CatalogueStore, dealguard_decision: dict,
                       previous: dict | None = None) -> dict:
    """One preview, several lines. `items` = [{product_id, quantity}] of products the customer named exactly."""
    lines = []
    for it in items:
        p = store.get_product(it["product_id"])
        unit = p["price"].get("amount")
        lines.append({"product_id": p["product_id"], "name": p["short_name"], "quantity": it["quantity"],
                      "unit_price": unit, "line_total": unit * it["quantity"] if unit is not None else None})
    total = sum(x["line_total"] for x in lines) if all(x["line_total"] is not None for x in lines) else None
    first = store.get_product(lines[0]["product_id"])
    version = (previous["version"] + 1) if previous else 1
    return {
        "preview_id": f"prev_{uuid.uuid4().hex[:10]}", "version": version, "order_type": "MOCK",
        "product_id": first["product_id"],
        "product_name": ", ".join(f"{x['quantity']} × {x['name']}" for x in lines),
        "items": lines, "quantity": sum(x["quantity"] for x in lines), "unit_price": None, "line_total": total,
        "currency": "INR", "price_status": first["price"].get("approval_status"), "price_claim_id": None,
        "dealguard_outcome": dealguard_decision.get("outcome"), "catalogue_version": store.version,
        "terms_unknown": ["GST / tax treatment", "delivery charges and timeline", "stock availability", "payment method"],
        "status": "PENDING_CONFIRMATION", "created_at": datetime.now(timezone.utc).isoformat(),
        "label": "SIMULATION — no payment is taken and no real order is placed",
    }


def confirm(session_id: str, preview: dict, confirmation_message_id: str) -> dict:
    """Create the MOCK order bound to this preview. Idempotent on (session, preview_id)."""
    idem = f"{session_id}:{preview['preview_id']}"
    order_id = f"MOCK-SGB-{uuid.uuid4().hex[:8].upper()}"
    res = db.create_mock_order(
        order_id=order_id, session_id=session_id, product_id=preview["product_id"],
        product_name=preview["product_name"], quantity=preview["quantity"], unit="each",
        quoted_price=preview["line_total"], currency=preview["currency"],
        catalogue_version=preview["catalogue_version"], confirmation_message_id=confirmation_message_id,
        idempotency_key=idem,
        details={"preview_id": preview["preview_id"], "preview_version": preview["version"],
                 "unit_price": preview["unit_price"], "price_status": preview["price_status"],
                 "terms_unknown": preview["terms_unknown"], "items": preview.get("items")},
    )
    if res["created"]:
        db.log_event(session_id, "ORDER_CREATED", {"order_id": order_id, "preview_id": preview["preview_id"],
                                                   "confirmation_message_id": confirmation_message_id})
    else:
        db.log_event(session_id, "DUPLICATE_CONFIRMATION_REJECTED", {"preview_id": preview["preview_id"],
                                                                     "existing_order": (res["order"] or {}).get("order_id")})
    return res
