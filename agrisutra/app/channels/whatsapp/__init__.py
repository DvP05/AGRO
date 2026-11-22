"""WhatsApp Business Platform (Cloud API) adapter.

Inbound:  Meta → POST /webhooks/whatsapp (signature-checked, deduplicated) → background worker →
          the same DialogueManager the browser simulator uses.
Outbound: dialogue payload → WhatsApp-formatted messages (text, reply buttons, lists, product images)
          → policy gates (human ownership, 24-hour window) → transport (dry-run by default, or live Graph API).
"""
