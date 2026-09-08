"""
LifeStore catalog resolver + authoritative pricing.

The payment flow must NEVER trust prices that pass through the LLM. Every price
used for a cart line or an order total is resolved here, from the *same* MCP
product data the rest of the Ask LifeStore agent uses. This guarantees the
amount charged always matches the live catalog and cannot be influenced by the
model's text output.

Product resolution reuses the existing LifeStore MCP tools via
``lifestore_mcp_tools._call_mcp_tool`` so there is a single source of truth for
product data (name, price, stock, url, image).
"""

from __future__ import annotations

import re
from typing import Any, Optional

from domain.tools.lifestore_mcp_tools import _call_mcp_tool


def _first_product(result: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Pull the first product object out of any MCP result shape."""
    if not isinstance(result, dict):
        return None

    single = result.get("product")
    if isinstance(single, dict):
        return single

    for key in ("products", "returned_products"):
        value = result.get(key)
        if isinstance(value, list) and value:
            first = value[0]
            if isinstance(first, dict):
                return first

    return None


async def resolve_product(product_query: str) -> Optional[dict[str, Any]]:
    """
    Resolve a free-text product reference to a single authoritative product.

    Tries the precise lookup first (best for named products / IDs / URLs), then
    falls back to the exact get-product tool, then a keyword search. Returns the
    raw MCP product dict, or ``None`` when nothing matches.
    """
    query = (product_query or "").strip()
    if not query:
        return None

    # 1. Precise lookup (hybrid / vector-aware single-product tool).
    try:
        result = await _call_mcp_tool(
            "lifestore_precise_product_lookup",
            product_query=query,
            include_vector_evidence=False,
        )
        product = _first_product(result)
        if product:
            return product
    except Exception:
        pass

    # 2. Exact get-product (id / sku / exact-or-partial name / url).
    try:
        result = await _call_mcp_tool("lifestore_get_product", product_id=query)
        product = _first_product(result)
        if product:
            return product
    except Exception:
        pass

    # 3. Keyword search — take the top hit.
    try:
        result = await _call_mcp_tool(
            "lifestore_search_products", q=query, limit=1
        )
        product = _first_product(result)
        if product:
            return product
    except Exception:
        pass

    return None


def price_to_cents(product: dict[str, Any]) -> Optional[int]:
    """
    Return the unit price in integer cents (LKR sub-units), or ``None`` when the
    product has no usable price. Prefers the numeric ``price_value`` field and
    only parses the display ``price`` string as a fallback.
    """
    if not isinstance(product, dict):
        return None

    price_value = product.get("price_value")
    if isinstance(price_value, (int, float)) and price_value > 0:
        return int(round(float(price_value) * 100))

    # Fallback: parse a display string like "Rs. 15,260.00" / "LKR 9,900".
    raw = str(product.get("price") or "").strip()
    if not raw:
        return None

    match = re.search(r"\d[\d,]*(?:\.\d+)?", raw)
    if not match:
        return None

    number = match.group(0).replace(",", "")
    try:
        value = float(number)
    except ValueError:
        return None

    if value <= 0:
        return None

    return int(round(value * 100))


def product_currency(product: dict[str, Any], default: str = "LKR") -> str:
    currency = str((product or {}).get("currency") or "").strip().upper()
    return currency or default


def product_in_stock(product: dict[str, Any]) -> bool:
    """Best-effort stock check used to warn (not silently block) on add."""
    status = str(
        (product or {}).get("stock_status")
        or (product or {}).get("availability")
        or ""
    ).lower().strip()
    stock = (product or {}).get("stock")

    if status in {"out_of_stock", "out of stock", "unavailable"} or stock == 0:
        return False
    return True


def compact_product_snapshot(product: dict[str, Any]) -> dict[str, Any]:
    """A minimal, frontend-safe product snapshot stored on the cart line."""
    return {
        "product_id": str(product.get("product_id") or product.get("sku") or "").strip(),
        "name": str(product.get("name") or "").strip(),
        "url": str(product.get("url") or "").strip(),
        "image_url": str(product.get("image_url") or "").strip(),
        "currency": product_currency(product),
    }
