"""AgriSutra — Evidence-First AI Sales Agent
Main application entry point.
"""

import os
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
    version="0.1.0",
)

catalogue_path = os.environ.get("CATALOGUE_PATH", "./data/catalogue/sample_catalogue.json")
store = CatalogueStore(catalogue_path)
manager = DialogueManager(store)
routes.init(store, manager)

# ── Register API routes ───────────────────────────────────────
app.include_router(routes.router)

# ── Serve frontend static files ───────────────────────────────
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
    print(f"  📦 Catalogue loaded: {len(store.products)} products")
    if store.warnings:
        print(f"  ⚠️  {len(store.warnings)} catalogue warning(s)")
    print("  🌐 UI: http://localhost:8000")
    print("  📡 API: http://localhost:8000/docs")
    print("=" * 60)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
