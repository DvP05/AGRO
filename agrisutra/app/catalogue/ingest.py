"""Catalogue ingestion, validation, and normalisation."""

import json
import os
from typing import Any


class CatalogueStore:
    """Loads, validates, and stores the product catalogue in memory."""

    def __init__(self, catalogue_path: str | None = None):
        self.path = catalogue_path or os.environ.get(
            "CATALOGUE_PATH", "./data/catalogue/sample_catalogue.json"
        )
        self.products: dict[str, dict] = {}
        self.warnings: list[str] = []
        self.version: str = "unknown"
        self._load()

    def _load(self):
        with open(self.path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        self.version = os.path.basename(self.path) + "-" + str(os.path.getmtime(self.path))
        for idx, item in enumerate(raw):
            warnings = self._validate(item, idx)
            self.warnings.extend(warnings)
            pid = item.get("product_id", f"UNKNOWN-{idx}")
            self.products[pid] = self._normalise(item)

    def _validate(self, item: dict, idx: int) -> list[str]:
        w = []
        if "product_id" not in item:
            w.append(f"Row {idx}: missing product_id")
        if "name" not in item:
            w.append(f"Row {idx}: missing name")
        if "price" in item:
            price = item["price"]
            if isinstance(price, dict) and price.get("amount") is None:
                w.append(f"Row {idx} ({item.get('product_id', '?')}): price amount is null")
        else:
            w.append(f"Row {idx} ({item.get('product_id', '?')}): no price field")
        return w

    def _normalise(self, item: dict) -> dict:
        """Return a canonical product record with safe defaults for missing fields."""
        return {
            "product_id": item.get("product_id"),
            "name": item.get("name", "Unknown Product"),
            "category": item.get("category", "Uncategorised"),
            "specifications": item.get("specifications", {}),
            "applications": item.get("applications", []),
            "price": item.get("price", {"amount": None, "currency": "INR", "unit": None}),
            "availability": item.get("availability", "UNKNOWN"),
            "limitations": item.get("limitations", []),
            "faq": item.get("faq", []),
            "source_ref": item.get("source_ref", "unknown"),
            "last_updated": item.get("last_updated"),
        }

    # ── Public API ────────────────────────────────────────────

    def get_product(self, product_id: str) -> dict | None:
        return self.products.get(product_id)

    def get_all_products(self) -> list[dict]:
        return list(self.products.values())

    def get_sprayer_products(self) -> list[dict]:
        """Return only sprayer products (not accessories)."""
        return [p for p in self.products.values() if p["category"] != "Accessories"]

    def search_by_name(self, query: str) -> list[dict]:
        q = query.lower()
        return [p for p in self.products.values() if q in p["name"].lower()]

    def get_categories(self) -> list[str]:
        return sorted(set(p["category"] for p in self.products.values()))

    def get_product_names(self) -> list[str]:
        return [p["name"] for p in self.products.values()]

    def get_field_value(self, product_id: str, field_path: str) -> Any:
        """Safely retrieve a nested field value from a product record.
        e.g. get_field_value('SGB-SPR-001', 'specifications.tank_capacity.value')
        """
        product = self.products.get(product_id)
        if not product:
            return None
        keys = field_path.split(".")
        current = product
        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return None
        return current
