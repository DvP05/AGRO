"""Mock order management with explicit confirmation and idempotency."""

import uuid
from app.catalogue.ingest import CatalogueStore
from app.storage import database as db


def generate_order_id() -> str:
    return f"MOCK-SGB-{uuid.uuid4().hex[:8].upper()}"


def create_order(session_id: str, product_id: str, product_name: str,
                 quantity: int, store: CatalogueStore,
                 confirmation_message_id: str) -> dict:
    """Create a mock order after explicit confirmation.
    
    Enforces:
    - No duplicate orders per session (idempotency)
    - Valid product ID from catalogue
    - Explicit confirmation message ID required
    """
    # Check for existing order (idempotency)
    existing = db.get_mock_order(session_id)
    if existing:
        return {
            "success": False,
            "error": "An order already exists for this session",
            "existing_order": existing,
        }

    # Validate product exists
    product = store.get_product(product_id)
    if not product:
        return {"success": False, "error": f"Product {product_id} not found in catalogue"}

    price = product.get("price", {}).get("amount")
    currency = product.get("price", {}).get("currency", "INR")

    order_id = generate_order_id()
    result = db.create_mock_order(
        order_id=order_id,
        session_id=session_id,
        product_id=product_id,
        product_name=product_name,
        quantity=quantity,
        unit=product.get("price", {}).get("unit", "piece"),
        quoted_price=price * quantity if price else None,
        currency=currency,
        catalogue_version=store.version,
        confirmation_message_id=confirmation_message_id,
        details={
            "product_category": product.get("category"),
            "availability_at_order": product.get("availability"),
        },
    )

    db.log_event(session_id, "ORDER_CREATED", {
        "order_id": order_id,
        "product_id": product_id,
        "quantity": quantity,
        "confirmation_message_id": confirmation_message_id,
    })

    return {
        "success": True,
        "order_id": order_id,
        "product_name": product_name,
        "quantity": quantity,
        "quoted_price": price * quantity if price else None,
        "currency": currency,
        "payment_status": "PENDING",
        "order_type": "MOCK",
        **result,
    }
