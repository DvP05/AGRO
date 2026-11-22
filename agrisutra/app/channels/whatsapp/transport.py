"""Outbound transports. Dry-run records exactly what would be sent; live calls the Cloud API."""

import os
import time
import uuid

import httpx

from app.channels.whatsapp import store
from app.channels.whatsapp.config import WhatsAppConfig

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


class SendResult(dict):
    """{status: SENT|DRY_RUN|FAILED, provider_message_id, error}"""


def _strip_private(msg: dict) -> dict:
    return {k: v for k, v in msg.items() if not k.startswith("_")}


class DryRunTransport:
    name = "dry_run"

    def __init__(self, cfg: WhatsAppConfig):
        self.cfg = cfg

    def resolve_media(self, msg: dict) -> dict:
        if msg.get("type") == "image" and self.cfg.public_base_url:
            msg["image"]["link"] = self.cfg.public_base_url + msg["_asset"]["url"]
        return msg

    def send(self, to: str, msg: dict) -> SendResult:
        return SendResult(status="DRY_RUN", provider_message_id=f"dryrun.{uuid.uuid4().hex[:16]}", error=None)


class GraphTransport(DryRunTransport):
    """WhatsApp Cloud API: POST /{version}/{phone_number_id}/messages with a bearer token."""
    name = "live"
    RETRY_STATUSES = {429, 500, 502, 503, 504}

    def __init__(self, cfg: WhatsAppConfig, client: httpx.Client | None = None):
        super().__init__(cfg)
        self.client = client or httpx.Client(timeout=15)

    @property
    def _base(self) -> str:
        return f"{self.cfg.graph_base}/{self.cfg.api_version}/{self.cfg.phone_number_id}"

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.cfg.access_token}"}

    def resolve_media(self, msg: dict) -> dict:
        if msg.get("type") != "image":
            return msg
        if self.cfg.public_base_url:
            msg["image"]["link"] = self.cfg.public_base_url + msg["_asset"]["url"]
            return msg
        # No public URL: upload the catalogue photo to Meta once and reuse its media id.
        sha = msg["_asset"].get("sha256") or ""
        media_id = store.media_get(sha) if sha else None
        if not media_id:
            path = os.path.join(ROOT, "data", msg["_asset"]["url"].lstrip("/"))
            with open(path, "rb") as f:
                r = self.client.post(f"{self._base}/media", headers=self._headers(),
                                     data={"messaging_product": "whatsapp", "type": "image/jpeg"},
                                     files={"file": (os.path.basename(path), f, "image/jpeg")})
            r.raise_for_status()
            media_id = r.json()["id"]
            if sha:
                store.media_put(sha, media_id)
        caption = msg["image"].get("caption")
        msg["image"] = {"id": media_id, **({"caption": caption} if caption else {})}
        return msg

    def send(self, to: str, msg: dict) -> SendResult:
        if self.cfg.problems() and (not self.cfg.access_token or not self.cfg.phone_number_id):
            return SendResult(status="FAILED", provider_message_id=None, error="live mode is not configured")
        body = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to, **_strip_private(msg)}
        last_err = None
        for attempt in range(3):
            try:
                r = self.client.post(f"{self._base}/messages", headers=self._headers(), json=body)
            except httpx.HTTPError as e:                     # network problem: retry
                last_err = str(e)
            else:
                if r.status_code < 300:
                    return SendResult(status="SENT", provider_message_id=r.json()["messages"][0]["id"], error=None)
                last_err = f"HTTP {r.status_code}: {r.text[:300]}"
                if r.status_code not in self.RETRY_STATUSES:
                    break                                    # policy / validation errors are permanent
            time.sleep(0.5 * (2 ** attempt))
        return SendResult(status="FAILED", provider_message_id=None, error=last_err)


def get_transport(cfg: WhatsAppConfig):
    return GraphTransport(cfg) if cfg.live else DryRunTransport(cfg)
