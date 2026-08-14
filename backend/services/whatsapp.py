"""Pure helpers and Cloud API client for the WhatsApp channel adapter."""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from typing import Any

import httpx


_EVIDENCE_BLOCK_RE = re.compile(
    r"\s*\[\[EVIDENCE_JSON\]\].*?\[\[/EVIDENCE_JSON\]\]\s*",
    flags=re.DOTALL,
)
_FRONTEND_TOKEN_RE = re.compile(r"\[RENDER_[A-Z0-9_]+\]")
_GRAPH_VERSION_RE = re.compile(r"^v\d+\.\d+$")
_DIGITS_RE = re.compile(r"\D+")


@dataclass(frozen=True)
class IncomingWhatsAppMessage:
    """Normalized inbound message extracted from a Meta webhook payload."""

    sender_id: str
    message_id: str
    text: str
    profile_name: str | None = None
    message_type: str = "text"


def normalize_phone_number(value: str | None) -> str:
    """Return the digits-only representation Meta uses for WhatsApp IDs."""
    return _DIGITS_RE.sub("", str(value or ""))


def verify_webhook_signature(raw_body: bytes, signature: str | None, app_secret: str) -> bool:
    """Validate Meta's ``X-Hub-Signature-256`` HMAC over the raw body."""
    if not signature or not signature.startswith("sha256=") or not app_secret:
        return False

    supplied = signature.removeprefix("sha256=")
    expected = hmac.new(
        app_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(supplied, expected)


def is_sender_allowed(sender_id: str, configured_allowlist: str) -> bool:
    """Allow every sender when the list is empty; otherwise require a match."""
    allowed = {
        normalize_phone_number(item)
        for item in (configured_allowlist or "").split(",")
        if normalize_phone_number(item)
    }
    return not allowed or normalize_phone_number(sender_id) in allowed


def pseudonymous_sender_key(sender_id: str, app_secret: str) -> str:
    """Create a stable non-reversible key for chat identity and thread state."""
    digest = hmac.new(
        app_secret.encode("utf-8"),
        normalize_phone_number(sender_id).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return digest[:32]


def extract_incoming_messages(payload: dict[str, Any]) -> list[IncomingWhatsAppMessage]:
    """Extract text/button/list replies from a WhatsApp webhook notification."""
    if payload.get("object") != "whatsapp_business_account":
        return []

    incoming: list[IncomingWhatsAppMessage] = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            if change.get("field") != "messages":
                continue
            value = change.get("value") or {}
            contact_names = {
                normalize_phone_number(contact.get("wa_id")): str(
                    (contact.get("profile") or {}).get("name") or ""
                ).strip()
                for contact in value.get("contacts") or []
            }

            for message in value.get("messages") or []:
                sender_id = normalize_phone_number(message.get("from"))
                message_id = str(message.get("id") or "").strip()
                message_type = str(message.get("type") or "unknown").strip()
                text = ""

                if message_type == "text":
                    text = str((message.get("text") or {}).get("body") or "").strip()
                elif message_type == "button":
                    text = str((message.get("button") or {}).get("text") or "").strip()
                elif message_type == "interactive":
                    interactive = message.get("interactive") or {}
                    reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
                    text = str(reply.get("title") or reply.get("id") or "").strip()

                if not sender_id or not message_id:
                    continue

                incoming.append(
                    IncomingWhatsAppMessage(
                        sender_id=sender_id,
                        message_id=message_id,
                        text=text,
                        profile_name=contact_names.get(sender_id) or None,
                        message_type=message_type,
                    )
                )

    return incoming


def extract_status_updates(payload: dict[str, Any]) -> list[tuple[str, str]]:
    """Return ``(message_id, status)`` pairs for lightweight delivery logging."""
    updates: list[tuple[str, str]] = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            for status in value.get("statuses") or []:
                message_id = str(status.get("id") or "").strip()
                state = str(status.get("status") or "").strip()
                if message_id and state:
                    updates.append((message_id, state))
    return updates


def clean_agent_reply(text: str) -> str:
    """Remove browser-only evidence/form contracts before sending to WhatsApp."""
    cleaned = _EVIDENCE_BLOCK_RE.sub("", text or "")
    cleaned = _FRONTEND_TOKEN_RE.sub("", cleaned)
    return cleaned.strip()


def split_agent_reply(text: str, limit: int = 3500) -> list[str]:
    """Split a completed agent response into conservatively sized messages."""
    remaining = clean_agent_reply(text)
    if not remaining:
        return []
    if limit < 100:
        raise ValueError("WhatsApp message split limit must be at least 100 characters")

    chunks: list[str] = []
    while len(remaining) > limit:
        candidates = (
            remaining.rfind("\n\n", 0, limit + 1),
            remaining.rfind("\n", 0, limit + 1),
            remaining.rfind(" ", 0, limit + 1),
        )
        split_at = max(candidates)
        if split_at < limit // 2:
            split_at = limit

        chunk = remaining[:split_at].strip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[split_at:].strip()

    if remaining:
        chunks.append(remaining)
    return chunks


async def send_text_message(
    *,
    recipient: str,
    text: str,
    access_token: str,
    phone_number_id: str,
    graph_api_version: str,
    reply_to_message_id: str | None = None,
) -> str:
    """Send one text message through Meta Cloud API and return its message ID."""
    recipient = normalize_phone_number(recipient)
    phone_number_id = normalize_phone_number(phone_number_id)
    if not recipient or not phone_number_id:
        raise ValueError("WhatsApp recipient and phone-number ID are required")
    if not _GRAPH_VERSION_RE.fullmatch(graph_api_version or ""):
        raise ValueError("WHATSAPP_GRAPH_API_VERSION must look like v23.0")

    payload: dict[str, Any] = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient,
        "type": "text",
        "text": {"preview_url": False, "body": text},
    }
    if reply_to_message_id:
        payload["context"] = {"message_id": reply_to_message_id}

    url = (
        f"https://graph.facebook.com/{graph_api_version}/"
        f"{phone_number_id}/messages"
    )
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        data = response.json()

    messages = data.get("messages") or []
    return str(messages[0].get("id") or "") if messages else ""
