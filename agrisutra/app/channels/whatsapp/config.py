"""WhatsApp settings, read from the environment on every call so tests and operators can change them."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class WhatsAppConfig:
    enabled: bool
    send_mode: str            # "dry_run" (default: record, never send) or "live" (call the Graph API)
    verify_token: str | None
    app_secret: str | None
    access_token: str | None
    phone_number_id: str | None
    api_version: str
    graph_base: str
    public_base_url: str | None
    allow_unsigned: bool      # local development only; never in production

    @property
    def live(self) -> bool:
        return self.send_mode == "live"

    def problems(self) -> list[str]:
        """Configuration gaps, shown in the cockpit and refused at send time in live mode."""
        out = []
        if not self.verify_token:
            out.append("WHATSAPP_VERIFY_TOKEN is not set — Meta cannot verify the webhook.")
        if not self.app_secret and not self.allow_unsigned:
            out.append("WHATSAPP_APP_SECRET is not set — inbound webhooks will be rejected (signature check).")
        if self.allow_unsigned:
            out.append("WHATSAPP_ALLOW_UNSIGNED=true — signature checks are OFF. Local testing only.")
        if self.live:
            if not self.access_token:
                out.append("WHATSAPP_ACCESS_TOKEN is not set — live sending is impossible.")
            if not self.phone_number_id:
                out.append("WHATSAPP_PHONE_NUMBER_ID is not set — live sending is impossible.")
            if not self.public_base_url:
                out.append("PUBLIC_BASE_URL is not set — product images will be uploaded to Meta instead of linked.")
        return out

    def public(self) -> dict:
        return {"enabled": self.enabled, "send_mode": self.send_mode, "api_version": self.api_version,
                "phone_number_id_set": bool(self.phone_number_id), "access_token_set": bool(self.access_token),
                "app_secret_set": bool(self.app_secret), "verify_token_set": bool(self.verify_token),
                "public_base_url": self.public_base_url, "allow_unsigned": self.allow_unsigned,
                "webhook_path": "/webhooks/whatsapp", "problems": self.problems()}


def _flag(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def get_config() -> WhatsAppConfig:
    mode = os.environ.get("WHATSAPP_SEND_MODE", "dry_run").strip().lower()
    return WhatsAppConfig(
        enabled=_flag("WHATSAPP_ENABLED", "true"),
        send_mode="live" if mode == "live" else "dry_run",
        verify_token=os.environ.get("WHATSAPP_VERIFY_TOKEN") or None,
        app_secret=os.environ.get("WHATSAPP_APP_SECRET") or None,
        access_token=os.environ.get("WHATSAPP_ACCESS_TOKEN") or None,
        phone_number_id=os.environ.get("WHATSAPP_PHONE_NUMBER_ID") or None,
        api_version=os.environ.get("WHATSAPP_API_VERSION", "v25.0"),
        graph_base=os.environ.get("WHATSAPP_GRAPH_BASE", "https://graph.facebook.com").rstrip("/"),
        public_base_url=(os.environ.get("PUBLIC_BASE_URL") or "").rstrip("/") or None,
        allow_unsigned=_flag("WHATSAPP_ALLOW_UNSIGNED"),
    )
