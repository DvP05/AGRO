"""Shared test setup: isolated temp database and a fresh manager per test."""

import os
import sys
import tempfile
import uuid

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

from app.storage import database as db  # noqa: E402

_TMP = tempfile.mkdtemp(prefix="agrisutra-tests-")
db.DB_PATH = os.path.join(_TMP, "test.db")

from app.catalogue.ingest import CatalogueStore  # noqa: E402
from app.dialogue.manager import DialogueManager  # noqa: E402

STORE = CatalogueStore(os.path.join(ROOT, "data", "catalogue", "sgb_catalogue.json"))


def new_manager() -> DialogueManager:
    return DialogueManager(STORE)


def new_session() -> str:
    sid = f"test-{uuid.uuid4().hex[:8]}"
    db.create_session(sid)
    return sid


def chat(manager: DialogueManager, sid: str, *lines: str) -> list[dict]:
    return [manager.process_message(sid, line) for line in lines]
