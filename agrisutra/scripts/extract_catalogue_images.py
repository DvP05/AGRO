"""Extract product images from the official SGB catalogue PDF (M0 — product truth).

Reads data/catalogue/source/<source_file>, writes one image per product to
data/assets/products/<PRODUCT_ID>.jpg and records page, embedded image name and
SHA-256 for each asset in data/assets/products/manifest.json. The catalogue
loader reads that manifest, so images stay traceable to their source page.

Usage:  python scripts/extract_catalogue_images.py
"""

import hashlib
import io
import json
import os
import sys

from PIL import Image
from pypdf import PdfReader

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CATALOGUE = os.path.join(ROOT, "data", "catalogue", "sgb_catalogue.json")
OUT_DIR = os.path.join(ROOT, "data", "assets", "products")
MAX_SIDE = 900          # keep web assets light; originals stay inside the PDF

# (page, index of embedded image on that page) → product. Page 1 shows the sidepack
# trolley on the left and the backpack trolley on the right, in that embedding order.
IMAGE_MAP = {
    "BCT-SIDE": (1, 0),
    "BCT-BACK": (1, 1),
    "WB-DUMPER": (2, 0),
    "WB-NORMAL": (3, 0),
    "VARSHA-TROLLEY": (4, 0),
    "143R-STABLE": (5, 0),
    "143R-WALBRO": (6, 0),
}


def main() -> int:
    with open(CATALOGUE, encoding="utf-8") as f:
        source_file = json.load(f)["source_file"]
    pdf_path = os.path.join(ROOT, "data", "catalogue", "source", source_file)
    if not os.path.isfile(pdf_path):
        print(f"Source PDF not found: {pdf_path}")
        return 1
    pdf_bytes = open(pdf_path, "rb").read()
    reader = PdfReader(pdf_path)
    os.makedirs(OUT_DIR, exist_ok=True)

    manifest = {"source_file": source_file, "source_sha256": hashlib.sha256(pdf_bytes).hexdigest(), "images": {}}
    for pid, (page_no, idx) in IMAGE_MAP.items():
        img = reader.pages[page_no - 1].images[idx]
        im = Image.open(io.BytesIO(img.data)).convert("RGB")
        orig = im.size
        im.thumbnail((MAX_SIDE, MAX_SIDE))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=86, optimize=True)
        data = buf.getvalue()
        out = os.path.join(OUT_DIR, f"{pid}.jpg")
        with open(out, "wb") as f:
            f.write(data)
        manifest["images"][pid] = {
            "asset": f"assets/products/{pid}.jpg", "page": page_no, "embedded_name": img.name,
            "embedded_index": idx, "original_size": list(orig), "size": list(im.size),
            "sha256": hashlib.sha256(data).hexdigest(), "status": "EXTRACTED",
        }
        print(f"{pid:16s} p.{page_no} {img.name:48s} {orig[0]}x{orig[1]} → {im.size[0]}x{im.size[1]}")

    with open(os.path.join(OUT_DIR, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print(f"Wrote {len(manifest['images'])} images and manifest to {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
