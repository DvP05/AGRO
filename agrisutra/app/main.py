"""AgriSutra — Evidence-First AI Sales Agent
Main application entry point.
"""

import os
import sys

# Ensure UTF-8 output on Windows consoles
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from dotenv import load_dotenv

load_dotenv()

from app.catalogue.ingest import CatalogueStore
from app.dialogue.manager import DialogueManager
from app.api import routes

# ── Initialise ────────────────────────────────────────────────
app = FastAPI(
    title="AgriSutra",
    description="Evidence-First AI Sales Agent for SGB Agro Industries",
    version="0.2.0",
)

catalogue_path = os.environ.get("CATALOGUE_PATH", "./data/catalogue/sgb_catalogue.json")
store = CatalogueStore(catalogue_path)
manager = DialogueManager(store)
routes.init(store, manager)

from app.channels.whatsapp import service as whatsapp_service, webhook as whatsapp_webhook  # noqa: E402
whatsapp_service.init(manager)

# ── Register API routes ───────────────────────────────────────
app.include_router(routes.router)
app.include_router(whatsapp_webhook.router)          # /webhooks/whatsapp + /api/v1/whatsapp/*

# ── Serve frontend static files ───────────────────────────────
# Product images extracted from the official catalogue PDF (see scripts/extract_catalogue_images.py).
assets_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "assets"))
if os.path.isdir(assets_dir):
    app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

web_dir = os.path.join(os.path.dirname(__file__), "web")
if os.path.isdir(web_dir):
    app.mount("/static", StaticFiles(directory=web_dir), name="static")

    @app.get("/")
    async def serve_index():
        return FileResponse(os.path.join(web_dir, "index.html"))


# ── Startup logging ──────────────────────────────────────────
@app.on_event("startup")
async def startup():
    print("=" * 60)
    print("  🌱 AgriSutra — Evidence-First AI Sales Agent")
    print(f"  📦 Catalogue {store.version}: {len(store.products)} products, {len(store.flags)} source flag(s)")
    for w in store.warnings:
        print(f"  ⚠️  {w}")
    from app.nlu.model import get_model
    print(f"  🧠 Intent model: {get_model()['model_version']}")
    print("  🌐 UI: http://localhost:8000")
    print("  📡 API: http://localhost:8000/docs")
    from app.channels.whatsapp.config import get_config
    wa = get_config()
    print(f"  💬 WhatsApp webhook: /webhooks/whatsapp · mode={wa.send_mode} · API {wa.api_version}")
    for p in wa.problems():
        print(f"     ⚠️  {p}")
    pending = whatsapp_service.process_pending()      # finish events received before a restart
    if pending:
        print(f"     ↻ processed {pending} pending WhatsApp event(s)")
    print("=" * 60)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
