"""WhatsApp Business Platform webhook backed by the Workmate Supervisor."""

from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from core.config import settings
from routers.chat import chat as run_chat_route
from schemas.chat import ChatRequest
from services.whatsapp import (
    IncomingWhatsAppMessage,
    extract_incoming_messages,
    extract_status_updates,
    is_sender_allowed,
    pseudonymous_sender_key,
    send_text_message,
    split_agent_reply,
    verify_webhook_signature,
)


router = APIRouter(prefix="/api/v1/whatsapp", tags=["WhatsApp"])
logger = logging.getLogger(__name__)

_SEEN_MESSAGE_TTL_SECONDS = 24 * 60 * 60
_seen_message_ids: dict[str, float] = {}
_seen_lock = asyncio.Lock()
_sender_locks: dict[str, asyncio.Lock] = {}


def _require_webhook_settings() -> tuple[str, str]:
    verify_token = (settings.WHATSAPP_VERIFY_TOKEN or "").strip()
    app_secret = (settings.WHATSAPP_APP_SECRET or "").strip()
    if not verify_token or not app_secret:
        raise HTTPException(
            status_code=503,
            detail="WhatsApp webhook verification is not configured.",
        )
    return verify_token, app_secret


def _require_send_settings() -> tuple[str, str, str]:
    access_token = (settings.WHATSAPP_ACCESS_TOKEN or "").strip()
    phone_number_id = (settings.WHATSAPP_PHONE_NUMBER_ID or "").strip()
    graph_api_version = (settings.WHATSAPP_GRAPH_API_VERSION or "").strip()
    if not access_token or not phone_number_id or not graph_api_version:
        raise RuntimeError(
            "WhatsApp sending is not configured: access token, phone-number ID, "
            "and Graph API version are required."
        )
    return access_token, phone_number_id, graph_api_version


async def _claim_message(message_id: str) -> bool:
    """Best-effort, process-local protection against Meta webhook retries."""
    now = time.monotonic()
    async with _seen_lock:
        expired = [
            item_id
            for item_id, seen_at in _seen_message_ids.items()
            if now - seen_at > _SEEN_MESSAGE_TTL_SECONDS
        ]
        for item_id in expired:
            _seen_message_ids.pop(item_id, None)

        if message_id in _seen_message_ids:
            return False
        _seen_message_ids[message_id] = now
        return True


async def _collect_supervisor_reply(chat_request: ChatRequest) -> str:
    """Consume the existing streaming route while preserving its full behavior."""
    response = await run_chat_route(chat_request)
    parts: list[str] = []
    async for chunk in response.body_iterator:
        if isinstance(chunk, bytes):
            parts.append(chunk.decode("utf-8", errors="replace"))
        else:
            parts.append(str(chunk))
    return "".join(parts)


async def _send_reply(
    message: IncomingWhatsAppMessage,
    reply: str,
) -> None:
    access_token, phone_number_id, graph_api_version = _require_send_settings()
    chunks = split_agent_reply(reply)
    if not chunks:
        chunks = ["Sorry, I couldn't produce a response. Please try again."]

    for index, chunk in enumerate(chunks):
        outbound_id = await send_text_message(
            recipient=message.sender_id,
            text=chunk,
            access_token=access_token,
            phone_number_id=phone_number_id,
            graph_api_version=graph_api_version,
            reply_to_message_id=message.message_id if index == 0 else None,
        )
        logger.info(
            "WhatsApp reply accepted | inbound=%s outbound=%s chunk=%d/%d",
            message.message_id,
            outbound_id or "unknown",
            index + 1,
            len(chunks),
        )


async def _process_message(message: IncomingWhatsAppMessage) -> None:
    """Run one inbound message through the existing Supervisor chat path."""
    sender_lock = _sender_locks.setdefault(message.sender_id, asyncio.Lock())
    async with sender_lock:
        try:
            if not is_sender_allowed(
                message.sender_id,
                settings.WHATSAPP_ALLOWED_NUMBERS,
            ):
                logger.warning(
                    "Rejected WhatsApp sender not present in configured allowlist | inbound=%s",
                    message.message_id,
                )
                await _send_reply(
                    message,
                    "Workmate AI is available only to authorized SLT-MOBITEL employees.",
                )
                return

            if not message.text:
                await _send_reply(
                    message,
                    "For this initial Workmate AI test, please send a text message.",
                )
                return

            app_secret = (settings.WHATSAPP_APP_SECRET or "").strip()
            sender_key = pseudonymous_sender_key(message.sender_id, app_secret)
            chat_request = ChatRequest(
                message=message.text,
                agent_id="supervisor",
                user_id=f"whatsapp:{sender_key}",
                user_name=message.profile_name or "WhatsApp user",
                thread_id=f"wa:{sender_key}",
            )
            reply = await _collect_supervisor_reply(chat_request)
            await _send_reply(message, reply)
        except Exception:
            logger.exception(
                "WhatsApp message processing failed | inbound=%s type=%s",
                message.message_id,
                message.message_type,
            )


@router.get("/webhook", response_class=PlainTextResponse)
async def verify_webhook(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
) -> PlainTextResponse:
    """Answer Meta's one-time callback URL verification challenge."""
    verify_token, _ = _require_webhook_settings()
    if hub_mode == "subscribe" and hub_verify_token == verify_token and hub_challenge:
        return PlainTextResponse(hub_challenge, status_code=200)
    raise HTTPException(status_code=403, detail="Invalid WhatsApp verification request.")


@router.post("/webhook")
async def receive_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    """Verify, acknowledge, and asynchronously process Meta webhook events."""
    _, app_secret = _require_webhook_settings()
    raw_body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")
    if not verify_webhook_signature(raw_body, signature, app_secret):
        raise HTTPException(status_code=403, detail="Invalid WhatsApp webhook signature.")

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload.") from exc

    for message_id, status in extract_status_updates(payload):
        logger.info("WhatsApp delivery update | outbound=%s status=%s", message_id, status)

    accepted = 0
    for message in extract_incoming_messages(payload):
        if await _claim_message(message.message_id):
            background_tasks.add_task(_process_message, message)
            accepted += 1
        else:
            logger.info("Ignored duplicate WhatsApp webhook | inbound=%s", message.message_id)

    return JSONResponse({"status": "accepted", "messages_queued": accepted})
