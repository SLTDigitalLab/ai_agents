"""
Ask LifeStore payment endpoints (PayHere).

Routes (prefix /api/v1/lifestore):
    GET  /cart?thread_id=...            → current cart view (frontend panel)
    GET  /checkout/{order_id}           → authoritative PayHere checkout object
    POST /payhere/notify                → PayHere webhook (source of truth)
    GET  /orders/{order_id}             → order status (frontend polling)
    POST /orders/{order_id}/reconcile   → server-side status check via PayHere API

Everything money-related is computed here from the stored order snapshot, never
from client input. The webhook is the source of truth for PAID/FAILED.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from schemas.lifestore_payment import (
    CartLine,
    CartView,
    CheckoutData,
    OrderStatus,
)
from services import lifestore_payhere as payhere
from services import lifestore_payments_store as store

router = APIRouter(prefix="/api/v1/lifestore", tags=["LifeStore Payments"])
logger = logging.getLogger(__name__)


def _money(cents: int, currency: str = "LKR") -> str:
    symbol = "Rs." if currency.upper() == "LKR" else currency.upper()
    return f"{symbol} {cents / 100:,.2f}"


def _line_from_item(item: dict) -> CartLine:
    unit = int(item["unit_price_cents"])
    qty = int(item["quantity"])
    currency = item.get("currency", "LKR")
    return CartLine(
        product_id=item["product_id"],
        name=item["name"],
        quantity=qty,
        unit_price_cents=unit,
        unit_price_display=_money(unit, currency),
        line_total_cents=unit * qty,
        line_total_display=_money(unit * qty, currency),
        currency=currency,
        url=item.get("url") or "",
        image_url=item.get("image_url") or "",
    )


@router.get("/cart", response_model=CartView)
def get_cart(thread_id: str) -> CartView:
    items = store.get_cart_items(thread_id)
    lines = [_line_from_item(it) for it in items]
    subtotal = sum(l.line_total_cents for l in lines)
    currency = lines[0].currency if lines else "LKR"
    return CartView(
        thread_id=thread_id,
        currency=currency,
        item_count=sum(l.quantity for l in lines),
        lines=lines,
        subtotal_cents=subtotal,
        subtotal_display=_money(subtotal, currency),
        is_demo=True,
    )


@router.get("/checkout/{order_id}", response_model=CheckoutData)
def get_checkout(order_id: str) -> CheckoutData:
    if not payhere.is_configured():
        raise HTTPException(
            status_code=503,
            detail="PayHere is not configured. Set PAYHERE_MERCHANT_ID and "
                   "PAYHERE_MERCHANT_SECRET in .env.",
        )

    order = store.get_order(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found.")

    items = order.get("items_json") or []
    lines = [_line_from_item(it) for it in items]
    currency = order["currency"]

    return CheckoutData(
        order_id=order_id,
        status=order["status"],
        amount_cents=int(order["amount_cents"]),
        amount_display=_money(int(order["amount_cents"]), currency),
        currency=currency,
        is_demo=bool(order["is_demo"]),
        lines=lines,
        payhere=payhere.build_checkout_payload(order),
    )


@router.post("/payhere/notify")
async def payhere_notify(request: Request) -> PlainTextResponse:
    """
    PayHere server-to-server callback. Verify the md5sig, then transition the
    order idempotently. Always return 200 for verified events so PayHere stops
    retrying; return 400 only for a failed signature.
    """
    form = dict((await request.form()))
    valid, order_id, status = payhere.verify_notify(form)

    if not valid:
        logger.warning("PayHere notify signature verification FAILED | order=%s", order_id)
        return PlainTextResponse("invalid signature", status_code=400)

    if not order_id or not status:
        logger.info("PayHere notify ignored (unmapped status) | order=%s | code=%s",
                    order_id, form.get("status_code"))
        return PlainTextResponse("ignored", status_code=200)

    if status == store.STATUS_PENDING:
        return PlainTextResponse("pending", status_code=200)

    changed, current = store.mark_order_status(
        order_id, status, provider_payment_id=str(form.get("payment_id") or "") or None
    )
    logger.info(
        "PayHere notify | order=%s | status=%s | changed=%s | now=%s",
        order_id, status, changed, current,
    )
    return PlainTextResponse("ok", status_code=200)


@router.get("/orders/{order_id}", response_model=OrderStatus)
def get_order_status(order_id: str) -> OrderStatus:
    order = store.get_order(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found.")
    currency = order["currency"]
    return OrderStatus(
        order_id=order_id,
        status=order["status"],
        amount_cents=int(order["amount_cents"]),
        amount_display=_money(int(order["amount_cents"]), currency),
        currency=currency,
        is_demo=bool(order["is_demo"]),
    )


@router.post("/orders/{order_id}/reconcile", response_model=OrderStatus)
async def reconcile_order(order_id: str) -> OrderStatus:
    """
    Authoritative server-side status check via PayHere's Retrieval API. Used by
    the frontend as a fallback when the webhook can't reach a local server. If
    retrieval isn't configured or returns nothing, the stored status is
    returned unchanged (never fabricated).
    """
    order = store.get_order(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found.")

    if order["status"] == store.STATUS_PENDING:
        remote_status = await payhere.reconcile_order(order_id)
        if remote_status in store.TERMINAL_STATUSES:
            store.mark_order_status(order_id, remote_status)
            order = store.get_order(order_id)

    currency = order["currency"]
    return OrderStatus(
        order_id=order_id,
        status=order["status"],
        amount_cents=int(order["amount_cents"]),
        amount_display=_money(int(order["amount_cents"]), currency),
        currency=currency,
        is_demo=bool(order["is_demo"]),
    )
