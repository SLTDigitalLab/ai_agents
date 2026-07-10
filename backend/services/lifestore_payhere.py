"""
PayHere gateway integration for Ask LifeStore (sandbox by default).

Two independent, server-side proofs of payment — the LLM is never involved:

1. ``notify`` (webhook)   — PayHere POSTs to our ``notify_url`` after payment.
   We verify the ``md5sig`` and update the order. This is the production source
   of truth. Requires a publicly reachable ``notify_url``.

2. ``reconcile`` (poll)   — a server-to-server call to PayHere's Payment
   Retrieval API (OAuth) that returns the authoritative status for an order.
   Used as a fallback when the webhook can't reach us (e.g. localhost) and only
   works when ``PAYHERE_APP_ID`` / ``PAYHERE_APP_SECRET`` are configured.

Hashing rules (PayHere docs):
* checkout hash =
    UPPER(MD5( merchant_id + order_id + amount + currency + UPPER(MD5(secret)) ))
* notify md5sig =
    UPPER(MD5( merchant_id + order_id + payhere_amount + payhere_currency
               + status_code + UPPER(MD5(secret)) ))
Amounts are formatted to 2 decimals with no thousands separators.
"""

from __future__ import annotations

import hashlib
from typing import Any, Optional

import httpx

from core.config import settings

# ── Config (from settings / .env) ──────────────────────────────────────────
MERCHANT_ID = (settings.PAYHERE_MERCHANT_ID or "").strip()
MERCHANT_SECRET = (settings.PAYHERE_MERCHANT_SECRET or "").strip()
SANDBOX = bool(settings.PAYHERE_SANDBOX)

APP_ID = (settings.PAYHERE_APP_ID or "").strip()
APP_SECRET = (settings.PAYHERE_APP_SECRET or "").strip()

APP_BASE_URL = (settings.APP_BASE_URL or "http://localhost:8000").rstrip("/")
FRONTEND_BASE_URL = (settings.FRONTEND_BASE_URL or "http://localhost:3000").rstrip("/")

# ``notify_url`` must be publicly reachable in production. Override with
# PAYHERE_NOTIFY_URL (e.g. an ngrok/tunnel URL) for local webhook testing.
NOTIFY_URL = (
    (settings.PAYHERE_NOTIFY_URL or "").strip()
    or f"{APP_BASE_URL}/api/v1/lifestore/payhere/notify"
)

_BASE = "https://sandbox.payhere.lk" if SANDBOX else "https://www.payhere.lk"
CHECKOUT_URL = f"{_BASE}/pay/checkout"

# PayHere status_code → our order status.
_STATUS_MAP = {
    "2": "PAID",
    "0": "PENDING",
    "-1": "CANCELED",
    "-2": "FAILED",
    "-3": "FAILED",  # chargedback
}


def is_configured() -> bool:
    return bool(MERCHANT_ID and MERCHANT_SECRET)


def retrieval_configured() -> bool:
    return bool(APP_ID and APP_SECRET)


def format_amount(amount_cents: int) -> str:
    """Cents → PayHere amount string, e.g. 1500000 → '15000.00'."""
    return f"{amount_cents / 100:.2f}"


def _md5_upper(value: str) -> str:
    return hashlib.md5(value.encode("utf-8")).hexdigest().upper()


def checkout_hash(order_id: str, amount_cents: int, currency: str) -> str:
    amount = format_amount(amount_cents)
    return _md5_upper(
        MERCHANT_ID + order_id + amount + currency + _md5_upper(MERCHANT_SECRET)
    )


def build_checkout_payload(order: dict[str, Any]) -> dict[str, Any]:
    """
    Build the authoritative object the frontend hands to PayHere's onsite
    checkout (payhere.startPayment). Amount, order_id, currency and hash are all
    server-generated so the client can never tamper with the charged amount.
    """
    order_id = order["order_id"]
    amount_cents = int(order["amount_cents"])
    currency = order["currency"]
    items = order.get("items_json") or []

    if isinstance(items, list) and items:
        item_label = items[0].get("name", "LifeStore order")
        if len(items) > 1:
            item_label = f"{item_label} + {len(items) - 1} more"
    else:
        item_label = "LifeStore order"

    return {
        "sandbox": SANDBOX,
        "merchant_id": MERCHANT_ID,
        "return_url": f"{FRONTEND_BASE_URL}/lifestore/return?order={order_id}",
        "cancel_url": f"{FRONTEND_BASE_URL}/lifestore/cancel?order={order_id}",
        "notify_url": NOTIFY_URL,
        "order_id": order_id,
        "items": item_label,
        "amount": format_amount(amount_cents),
        "currency": currency,
        "hash": checkout_hash(order_id, amount_cents, currency),
        "checkout_url": CHECKOUT_URL,
    }


def verify_notify(form: dict[str, Any]) -> tuple[bool, Optional[str], Optional[str]]:
    """
    Verify a PayHere notify callback.

    Returns ``(valid, order_id, status)`` where ``status`` is one of our order
    statuses. ``valid`` is False when the md5sig does not match — such calls
    MUST be ignored (possible spoof).
    """
    merchant_id = str(form.get("merchant_id", ""))
    order_id = str(form.get("order_id", ""))
    payhere_amount = str(form.get("payhere_amount", ""))
    payhere_currency = str(form.get("payhere_currency", ""))
    status_code = str(form.get("status_code", ""))
    received_sig = str(form.get("md5sig", "")).upper()

    local_sig = _md5_upper(
        merchant_id
        + order_id
        + payhere_amount
        + payhere_currency
        + status_code
        + _md5_upper(MERCHANT_SECRET)
    )

    if not received_sig or local_sig != received_sig or merchant_id != MERCHANT_ID:
        return False, order_id or None, None

    status = _STATUS_MAP.get(status_code)
    return True, order_id, status


async def reconcile_order(order_id: str) -> Optional[str]:
    """
    Ask PayHere directly for an order's status via the Payment Retrieval API.

    Returns one of our order statuses, or ``None`` when retrieval is not
    configured or the call fails. This is an authoritative server-to-server
    check — safe to trust — and is the fallback path when the webhook cannot
    reach a local dev server.
    """
    if not retrieval_configured():
        return None

    token_url = f"{_BASE}/merchant/v1/oauth/token"
    search_url = f"{_BASE}/merchant/v1/payment/search"

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            token_resp = await client.post(
                token_url,
                data={"grant_type": "client_credentials"},
                auth=(APP_ID, APP_SECRET),
            )
            token_resp.raise_for_status()
            access_token = token_resp.json().get("access_token")
            if not access_token:
                return None

            search_resp = await client.get(
                search_url,
                params={"order_id": order_id},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            search_resp.raise_for_status()
            payload = search_resp.json()
    except Exception:
        return None

    data = payload.get("data")
    if not isinstance(data, list) or not data:
        return None

    # status: 2 = success, 0 = pending, -1 canceled, -2 failed, -3 chargedback.
    latest = data[0]
    status_code = str(latest.get("status_code", latest.get("status", "")))
    return _STATUS_MAP.get(status_code)
