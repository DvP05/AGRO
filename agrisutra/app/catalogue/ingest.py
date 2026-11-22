"""Catalogue ingestion, validation and versioning (M0 — Product truth).

The catalogue JSON is a transcription of the SGB PDF. Every fact keeps its
source page/location and an evidence status. Ambiguous source material is kept
as an *observation* with its own status instead of being promoted to a fact.
"""

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any

DEFAULT_CATALOGUE = "./data/catalogue/sgb_catalogue.json"

FAMILY_LABELS = {
    "brush_cutter_trolley_attachment": "Brush Cutter Trolley (attachment)",
    "wheel_barrow": "Wheel Barrow",
    "brush_cutter_machine": "Brush Cutter (machine)",
}

VALID_FACT_STATUSES = {"APPROVED", "UNAPPROVED", "CONFLICTED"}


class CatalogueStore:
    """Loads, validates and versions the approved product catalogue."""

    def __init__(self, catalogue_path: str | None = None):
        self.path = catalogue_path or os.environ.get("CATALOGUE_PATH", DEFAULT_CATALOGUE)
        if not os.path.exists(self.path):
            self.path = DEFAULT_CATALOGUE
        self.products: dict[str, dict] = {}
        self.warnings: list[str] = []
        self.flags: list[dict] = []          # source anomalies that need SGB review
        self.meta: dict = {}
        self.version: str = "unknown"
        self.source_file: str | None = None
        self.source_hash: str | None = None
        self.imported_at: str = datetime.now(timezone.utc).isoformat()
        self._load()

    # ── Ingestion ─────────────────────────────────────────────

    def _load(self) -> None:
        with open(self.path, "rb") as f:
            raw_bytes = f.read()
        raw = json.loads(raw_bytes.decode("utf-8"))

        # Version = hash of the canonical normalised content (stable across whitespace edits).
        canonical = json.dumps(raw, sort_keys=True, ensure_ascii=False).encode("utf-8")
        self.version = "cat-" + hashlib.sha256(canonical).hexdigest()[:12]
        self.meta = {k: v for k, v in raw.items() if k != "products"}
        self.source_file = raw.get("source_file")

        source_pdf = os.path.join(os.path.dirname(self.path), "source", self.source_file or "")
        if self.source_file and os.path.isfile(source_pdf):
            with open(source_pdf, "rb") as f:
                self.source_hash = hashlib.sha256(f.read()).hexdigest()
        else:
            self.source_hash = None
            self.warnings.append(
                "Source PDF is not in data/catalogue/source/, so its SHA-256 cannot be computed. "
                "Facts were transcribed from the supplied specification documents."
            )

        if raw.get("price_effective_date") is None:
            self.warnings.append("Prices have no effective date or tax treatment; treat as catalogue-listed only.")

        self.images = self._load_image_manifest()

        for idx, item in enumerate(raw.get("products", [])):
            self.warnings.extend(self._validate(item, idx))
            product = self._normalise(item)
            self.products[product["product_id"]] = product
            for obs in product["observations"]:
                self.flags.append({
                    "product_id": product["product_id"],
                    "field": obs["field"],
                    "observed_value": obs["value"],
                    "status": obs["status"],
                    "source": obs["source"],
                    "note": obs.get("note"),
                })

    def _load_image_manifest(self) -> dict:
        """Product images extracted from the source PDF (scripts/extract_catalogue_images.py)."""
        root = os.path.abspath(os.path.join(os.path.dirname(self.path), "..", ".."))
        path = os.path.join(root, "data", "assets", "products", "manifest.json")
        if not os.path.isfile(path):
            self.warnings.append("No product images extracted yet — run scripts/extract_catalogue_images.py.")
            return {}
        with open(path, encoding="utf-8") as f:
            manifest = json.load(f)
        if self.source_hash and manifest.get("source_sha256") != self.source_hash:
            self.warnings.append("Product images were extracted from a different PDF version; re-run the extractor.")
        images = {}
        for pid, img in manifest.get("images", {}).items():
            if os.path.isfile(os.path.join(root, "data", img["asset"])):
                images[pid] = {**img, "url": "/" + img["asset"], "source_file": manifest.get("source_file")}
        return images

    def _validate(self, item: dict, idx: int) -> list[str]:
        w = []
        pid = item.get("product_id", f"row-{idx}")
        for key in ("product_id", "display_name", "family", "price", "facts"):
            if key not in item:
                w.append(f"{pid}: missing '{key}'")
        if item.get("family") not in FAMILY_LABELS:
            w.append(f"{pid}: unknown family '{item.get('family')}'")
        price = item.get("price") or {}
        if price.get("amount") is None:
            w.append(f"{pid}: price amount missing")
        for fkey, fact in (item.get("facts") or {}).items():
            if fact.get("status") not in VALID_FACT_STATUSES:
                w.append(f"{pid}.{fkey}: invalid status '{fact.get('status')}'")
            if not (fact.get("source") or {}).get("page"):
                w.append(f"{pid}.{fkey}: no source page")
        return w

    def _normalise(self, item: dict) -> dict:
        return {
            "product_id": item["product_id"],
            "name": item.get("display_name", item["product_id"]),
            "short_name": item.get("short_name", item.get("display_name", "")),
            "family": item.get("family"),
            "family_label": FAMILY_LABELS.get(item.get("family"), item.get("family")),
            "aliases": [a.lower() for a in item.get("aliases", [])],
            "price": item.get("price", {"amount": None, "currency": "INR"}),
            "facts": item.get("facts", {}),
            "observations": item.get("observations", []),
            "not_stated": item.get("not_stated", []),
            "availability": {"status": "UNKNOWN", "source_id": None, "checked_at": None},
            "image": self.images.get(item["product_id"]),
            "images": [self.images[item["product_id"]]] if item["product_id"] in self.images else
                      [{"page": item.get("price", {}).get("source", {}).get("page"), "asset": None, "status": "NOT_EXTRACTED"}],
            "source_file": self.source_file,
        }

    # ── Public API ────────────────────────────────────────────

    def get_product(self, product_id: str) -> dict | None:
        return self.products.get(product_id)

    def get_all_products(self) -> list[dict]:
        return list(self.products.values())

    def get_family(self, family: str) -> list[dict]:
        return [p for p in self.products.values() if p["family"] == family]

    def get_families(self) -> list[str]:
        return list(FAMILY_LABELS.keys())

    def get_product_names(self) -> list[str]:
        return [p["name"] for p in self.products.values()]

    def find_by_alias(self, text: str) -> list[dict]:
        """Return products whose alias or short name appears in the text (longest match first)."""
        t = text.lower()
        hits = []
        for p in self.products.values():
            names = p["aliases"] + [p["short_name"].lower(), p["name"].lower()]
            best = max((len(a) for a in names if a and a in t), default=0)
            if best:
                hits.append((best, p))
        hits.sort(key=lambda x: x[0], reverse=True)
        return [p for _, p in hits]

    def get_fact(self, product_id: str, field: str) -> dict | None:
        product = self.products.get(product_id)
        if not product:
            return None
        return product["facts"].get(field)

    def get_observation(self, product_id: str, field: str) -> dict | None:
        product = self.products.get(product_id)
        if not product:
            return None
        return next((o for o in product["observations"] if o["field"] == field), None)

    def get_field_value(self, product_id: str, field_path: str) -> Any:
        """Safe dotted-path access, e.g. 'price.amount' or 'facts.power.value'."""
        current: Any = self.products.get(product_id)
        for key in field_path.split("."):
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return None
        return current

    def validation_report(self) -> dict:
        return {
            "catalogue_version": self.version,
            "source_file": self.source_file,
            "source_hash": self.source_hash,
            "imported_at": self.imported_at,
            "product_count": len(self.products),
            "warnings": self.warnings,
            "flags": self.flags,
        }
