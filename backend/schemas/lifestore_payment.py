"""Pydantic response models for the Ask LifeStore payment endpoints."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class CartLine(BaseModel):
    product_id: str
    name: str
    quantity: int
    unit_price_cents: int
    unit_price_display: str
    line_total_cents: int
    line_total_display: str
    currency: str
    url: str = ""
    image_url: str = ""


class CartView(BaseModel):
    thread_id: str
    currency: str = "LKR"
    item_count: int = 0
    lines: list[CartLine] = []
    subtotal_cents: int = 0
    subtotal_display: str = "Rs. 0.00"
    is_demo: bool = True


class OrderStatus(BaseModel):
    order_id: str
    status: str
    amount_cents: int
    amount_display: str
    currency: str
    is_demo: bool = True


class CheckoutData(BaseModel):
    order_id: str
    status: str
    amount_cents: int
    amount_display: str
    currency: str
    is_demo: bool = True
    lines: list[CartLine] = []
    # PayHere onsite-checkout object (server-generated, hash-signed).
    payhere: dict[str, Any]
