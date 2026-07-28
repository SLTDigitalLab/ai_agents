"""
Ask LifeStore cart + checkout tools (chat-driven commerce).

These LangGraph tools let a customer build a cart and start a PayHere payment
entirely through chat. The design keeps the LLM away from anything financial:

* Prices are resolved server-side from the live catalog
  (``services.lifestore_catalog``) — never from the model's text.
* The cart is persisted in Postgres keyed by the chat ``thread_id`` (injected
  automatically via ``RunnableConfig``; the model never sees or sets it).
* ``begin_checkout`` snapshots the cart into an order with a server-computed
  total, then returns an opaque ``order_id``. The model only echoes a
  ``[RENDER_LIFESTORE_CHECKOUT:<order_id>]`` marker; the frontend fetches the
  authoritative amount + signed PayHere payload from the backend by that id, so
  the model can never invent a price or a payment link.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from services import lifestore_catalog as catalog
from services import lifestore_payments_store as store


def _cart_owner_id(config: RunnableConfig | None) -> str:
    """Return the chat thread_id used to store a LifeStore cart."""
    if not config:
        return "anonymous"
    configurable = config.get("configurable") or {}
    return str(configurable.get("thread_id") or "anonymous")


def _money(cents: int, currency: str = "LKR") -> str:
    symbol = "Rs." if currency.upper() == "LKR" else currency.upper()
    return f"{symbol} {cents / 100:,.2f}"


def _cart_summary(cart_owner_id: str) -> dict[str, Any]:
    items = store.get_cart_items(cart_owner_id)
    currency = items[0]["currency"] if items else "LKR"
    lines = []
    subtotal = 0
    for it in items:
        unit = int(it["unit_price_cents"])
        qty = int(it["quantity"])
        line_total = unit * qty
        subtotal += line_total
        lines.append(
            {
                "name": it["name"],
                "quantity": qty,
                "unit_price": _money(unit, currency),
                "line_total": _money(line_total, currency),
            }
        )
    return {
        "currency": currency,
        "item_count": sum(int(it["quantity"]) for it in items),
        "lines": lines,
        "subtotal": _money(subtotal, currency),
        "subtotal_cents": subtotal,
    }


def _to_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


@tool
async def lifestore_add_to_cart(
    product_query: str,
    quantity: int = 1,
    config: RunnableConfig = None,
) -> str:
    """
    Add a LifeStore product to the customer's cart by name, product ID, or URL.

    Use this when the customer clearly wants to add/buy a specific product
    (e.g. "add the COMSTOX ZLT T10 Max", "I'll take 2 of those"). The price is
    taken from the live catalog automatically — never state or guess a price.
    Returns the updated cart summary.
    """
    cart_owner_id = _cart_owner_id(config)
    qty = max(int(quantity or 1), 1)

    product = await catalog.resolve_product(product_query)
    if not product:
        return _to_json({
            "status": "product_not_found",
            "message": f"No LifeStore product matched '{product_query}'. "
                       "Ask the customer to confirm the exact product name.",
        })

    unit_cents = catalog.price_to_cents(product)
    if not unit_cents:
        return _to_json({
            "status": "no_price",
            "message": f"'{product.get('name') or product_query}' has no listed price, "
                       "so it can't be added to the cart.",
        })

    snapshot = catalog.compact_product_snapshot(product)
    product_id = snapshot["product_id"] or (snapshot["name"] or product_query).lower()
    in_stock = catalog.product_in_stock(product)

    await asyncio.to_thread(
        store.upsert_cart_item,
        cart_owner_id,
        product_id=product_id,
        name=snapshot["name"] or product_query,
        unit_price_cents=unit_cents,
        currency=snapshot["currency"],
        quantity=qty,
        url=snapshot["url"],
        image_url=snapshot["image_url"],
        add=True,
    )

    summary = await asyncio.to_thread(_cart_summary, cart_owner_id)
    return _to_json({
        "status": "added",
        "added": {
            "name": snapshot["name"] or product_query,
            "quantity": qty,
            "unit_price": _money(unit_cents, snapshot["currency"]),
        },
        "in_stock": in_stock,
        "stock_warning": None if in_stock else "This item is currently marked out of stock.",
        "cart": summary,
    })


@tool
async def lifestore_view_cart(config: RunnableConfig = None) -> str:
    """
    Show the customer's current LifeStore cart contents and subtotal.

    Use this when the customer asks what's in their cart or before checkout.
    """
    cart_owner_id = _cart_owner_id(config)
    summary = await asyncio.to_thread(_cart_summary, cart_owner_id)
    status = "cart_empty" if summary["item_count"] == 0 else "cart"
    return _to_json({"status": status, "cart": summary})


@tool
async def lifestore_update_cart_item(
    product_query: str,
    quantity: int,
    config: RunnableConfig = None,
) -> str:
    """
    Set the exact quantity of a product already in the cart. A quantity of 0
    removes it. Use this for "make it 3", "change to 1", etc.
    """
    cart_owner_id = _cart_owner_id(config)
    qty = int(quantity)

    product = await catalog.resolve_product(product_query)
    if not product:
        return _to_json({
            "status": "product_not_found",
            "message": f"No LifeStore product matched '{product_query}'.",
        })

    snapshot = catalog.compact_product_snapshot(product)
    product_id = snapshot["product_id"] or (snapshot["name"] or product_query).lower()

    if qty <= 0:
        await asyncio.to_thread(store.remove_cart_item, cart_owner_id, product_id)
    else:
        unit_cents = catalog.price_to_cents(product)
        if not unit_cents:
            return _to_json({
                "status": "no_price",
                "message": f"'{snapshot['name'] or product_query}' has no listed price.",
            })
        await asyncio.to_thread(
            store.upsert_cart_item,
            cart_owner_id,
            product_id=product_id,
            name=snapshot["name"] or product_query,
            unit_price_cents=unit_cents,
            currency=snapshot["currency"],
            quantity=qty,
            url=snapshot["url"],
            image_url=snapshot["image_url"],
            add=False,
        )

    summary = await asyncio.to_thread(_cart_summary, cart_owner_id)
    return _to_json({"status": "updated", "cart": summary})


@tool
async def lifestore_remove_from_cart(
    product_query: str,
    config: RunnableConfig = None,
) -> str:
    """Remove a product from the cart entirely by name, product ID, or URL."""
    cart_owner_id = _cart_owner_id(config)
    product = await catalog.resolve_product(product_query)
    product_id = ""
    if product:
        snapshot = catalog.compact_product_snapshot(product)
        product_id = snapshot["product_id"] or (snapshot["name"] or product_query).lower()
    else:
        product_id = product_query.lower()

    await asyncio.to_thread(store.remove_cart_item, cart_owner_id, product_id)
    summary = await asyncio.to_thread(_cart_summary, cart_owner_id)
    return _to_json({"status": "removed", "cart": summary})


@tool
async def lifestore_clear_cart(config: RunnableConfig = None) -> str:
    """Empty the customer's LifeStore cart completely."""
    cart_owner_id = _cart_owner_id(config)
    await asyncio.to_thread(store.clear_cart, cart_owner_id)
    return _to_json({"status": "cleared", "cart": _cart_summary(cart_owner_id)})


@tool
async def lifestore_begin_checkout(config: RunnableConfig = None) -> str:
    """
    Start payment for the current cart. Call this ONLY when the customer clearly
    wants to pay / check out and the cart is not empty.

    This re-prices every cart line from the live catalog, creates a pending
    order with a server-computed total, and returns an order_id. After calling
    it you MUST end your reply with exactly:
        [RENDER_LIFESTORE_CHECKOUT:<order_id>]
    using the order_id returned here. Do NOT write a payment link or the amount
    yourself — the secure PayHere checkout is rendered from the order_id.
    """
    cart_owner_id = _cart_owner_id(config)
    items = await asyncio.to_thread(store.get_cart_items, cart_owner_id)

    if not items:
        return _to_json({
            "status": "cart_empty",
            "message": "The cart is empty. Add a product before checking out.",
        })

    # Re-price every line from the live catalog so the amount sent to PayHere is
    # always fresh and authoritative (guards against stale cart prices).
    order_items: list[dict[str, Any]] = []
    amount_cents = 0
    currency = "LKR"

    for it in items:
        product = await catalog.resolve_product(it["product_id"] or it["name"])
        unit_cents = catalog.price_to_cents(product) if product else None
        if not unit_cents:
            # Fall back to the stored price rather than dropping the line.
            unit_cents = int(it["unit_price_cents"])
        currency = (
            catalog.product_currency(product) if product else it.get("currency", "LKR")
        )
        qty = int(it["quantity"])
        line_total = unit_cents * qty
        amount_cents += line_total
        order_items.append({
            "product_id": it["product_id"],
            "name": it["name"],
            "quantity": qty,
            "unit_price_cents": unit_cents,
            "line_total_cents": line_total,
            "currency": currency,
            "url": it.get("url") or "",
            "image_url": it.get("image_url") or "",
        })

    order_id = await asyncio.to_thread(
        store.create_order,
        cart_owner_id,
        items=order_items,
        amount_cents=amount_cents,
        currency=currency,
        is_demo=True,
    )

    return _to_json({
        "status": "checkout_ready",
        "order_id": order_id,
        "amount": _money(amount_cents, currency),
        "is_demo": True,
        "instruction": (
            "Tell the customer their order is ready to pay for the amount shown, "
            "note this is a sandbox demo payment (no real money is charged), then "
            f"end your reply with exactly: [RENDER_LIFESTORE_CHECKOUT:{order_id}]"
        ),
    })


LIFESTORE_CART_TOOLS = [
    lifestore_add_to_cart,
    lifestore_view_cart,
    lifestore_update_cart_item,
    lifestore_remove_from_cart,
    lifestore_clear_cart,
    lifestore_begin_checkout,
]
