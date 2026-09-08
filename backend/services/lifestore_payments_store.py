"""
Postgres-backed cart + order store for the Ask LifeStore payment flow.

Design notes
------------
* Keyed by the chat ``thread_id`` — the same identity used by LangGraph
  checkpointing and the frontend. This is the cross-layer key that ties a chat
  session to its cart and orders.
* Money is stored as integer cents to avoid floating-point rounding.
* Order status transitions are **idempotent**: an order only moves out of
  PENDING once, so PayHere webhook retries (at-least-once delivery) can never
  double-apply or flip a settled order.
* All functions are synchronous psycopg calls (autocommit), mirroring
  ``core.checkpointer._ensure_schema``. Async callers should wrap them in
  ``asyncio.to_thread`` to avoid blocking the event loop.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Optional

import psycopg
from psycopg.rows import dict_row

from core.config import settings

SCHEMA = "lifestore_payments"

# Order lifecycle.
STATUS_PENDING = "PENDING"
STATUS_PAID = "PAID"
STATUS_FAILED = "FAILED"
STATUS_CANCELED = "CANCELED"
TERMINAL_STATUSES = {STATUS_PAID, STATUS_FAILED, STATUS_CANCELED}

_TABLES_READY = False


def _connect() -> psycopg.Connection:
    return psycopg.connect(settings.POSTGRES_URL, autocommit=True, row_factory=dict_row)


def ensure_tables() -> None:
    """Create the schema + tables once per process. Safe to call repeatedly."""
    global _TABLES_READY
    if _TABLES_READY:
        return

    with _connect() as conn, conn.cursor() as cur:
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA}.cart_items (
                id               BIGSERIAL PRIMARY KEY,
                thread_id        TEXT   NOT NULL,
                product_id       TEXT   NOT NULL,
                name             TEXT   NOT NULL,
                unit_price_cents BIGINT NOT NULL,
                currency         TEXT   NOT NULL DEFAULT 'LKR',
                quantity         INT    NOT NULL CHECK (quantity > 0),
                url              TEXT,
                image_url        TEXT,
                created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (thread_id, product_id)
            )
            """
        )
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA}.orders (
                order_id            TEXT PRIMARY KEY,
                thread_id           TEXT   NOT NULL,
                status              TEXT   NOT NULL DEFAULT 'PENDING',
                amount_cents        BIGINT NOT NULL,
                currency            TEXT   NOT NULL DEFAULT 'LKR',
                items_json          JSONB  NOT NULL,
                provider            TEXT   NOT NULL DEFAULT 'payhere',
                provider_payment_id TEXT,
                is_demo             BOOLEAN NOT NULL DEFAULT TRUE,
                created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
                paid_at             TIMESTAMPTZ
            )
            """
        )
        cur.execute(
            f"CREATE INDEX IF NOT EXISTS orders_thread_idx "
            f"ON {SCHEMA}.orders (thread_id)"
        )

    _TABLES_READY = True


# ── Cart ──────────────────────────────────────────────────────────────────
def get_cart_items(thread_id: str) -> list[dict[str, Any]]:
    ensure_tables()
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT product_id, name, unit_price_cents, currency, quantity, url, image_url
            FROM {SCHEMA}.cart_items
            WHERE thread_id = %s
            ORDER BY created_at
            """,
            (thread_id,),
        )
        return list(cur.fetchall())


def upsert_cart_item(
    thread_id: str,
    *,
    product_id: str,
    name: str,
    unit_price_cents: int,
    currency: str,
    quantity: int,
    url: str = "",
    image_url: str = "",
    add: bool = True,
) -> None:
    """
    Add to (``add=True``) or set (``add=False``) a cart line's quantity.

    Always refreshes name/price/url/image to the latest resolved values so a
    cart never holds a stale price.
    """
    ensure_tables()
    with _connect() as conn, conn.cursor() as cur:
        if add:
            cur.execute(
                f"""
                INSERT INTO {SCHEMA}.cart_items
                    (thread_id, product_id, name, unit_price_cents, currency,
                     quantity, url, image_url)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (thread_id, product_id) DO UPDATE SET
                    quantity         = {SCHEMA}.cart_items.quantity + EXCLUDED.quantity,
                    name             = EXCLUDED.name,
                    unit_price_cents = EXCLUDED.unit_price_cents,
                    currency         = EXCLUDED.currency,
                    url              = EXCLUDED.url,
                    image_url        = EXCLUDED.image_url,
                    updated_at       = now()
                """,
                (thread_id, product_id, name, unit_price_cents, currency,
                 quantity, url, image_url),
            )
        else:
            cur.execute(
                f"""
                INSERT INTO {SCHEMA}.cart_items
                    (thread_id, product_id, name, unit_price_cents, currency,
                     quantity, url, image_url)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (thread_id, product_id) DO UPDATE SET
                    quantity         = EXCLUDED.quantity,
                    name             = EXCLUDED.name,
                    unit_price_cents = EXCLUDED.unit_price_cents,
                    currency         = EXCLUDED.currency,
                    url              = EXCLUDED.url,
                    image_url        = EXCLUDED.image_url,
                    updated_at       = now()
                """,
                (thread_id, product_id, name, unit_price_cents, currency,
                 quantity, url, image_url),
            )


def remove_cart_item(thread_id: str, product_id: str) -> int:
    ensure_tables()
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"DELETE FROM {SCHEMA}.cart_items WHERE thread_id = %s AND product_id = %s",
            (thread_id, product_id),
        )
        return cur.rowcount


def clear_cart(thread_id: str) -> None:
    ensure_tables()
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"DELETE FROM {SCHEMA}.cart_items WHERE thread_id = %s", (thread_id,)
        )


# ── Orders ────────────────────────────────────────────────────────────────
def create_order(
    thread_id: str,
    *,
    items: list[dict[str, Any]],
    amount_cents: int,
    currency: str,
    is_demo: bool = True,
) -> str:
    ensure_tables()
    order_id = f"LS-{uuid.uuid4().hex[:16].upper()}"
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO {SCHEMA}.orders
                (order_id, thread_id, status, amount_cents, currency, items_json, is_demo)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (order_id, thread_id, STATUS_PENDING, amount_cents, currency,
             json.dumps(items), is_demo),
        )
    return order_id


def get_order(order_id: str) -> Optional[dict[str, Any]]:
    ensure_tables()
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT * FROM {SCHEMA}.orders WHERE order_id = %s", (order_id,)
        )
        return cur.fetchone()


def mark_order_status(
    order_id: str,
    status: str,
    *,
    provider_payment_id: Optional[str] = None,
) -> tuple[bool, str]:
    """
    Idempotently move an order to a terminal status.

    Returns ``(changed, current_status)``:
    * ``changed`` is True only when this call actually transitioned the order.
    * If the order is already terminal, the status is left untouched and
      ``changed`` is False — this is what makes webhook retries safe.
    """
    ensure_tables()
    if status not in TERMINAL_STATUSES:
        raise ValueError(f"Invalid terminal order status: {status}")

    paid_clause = ", paid_at = now()" if status == STATUS_PAID else ""

    with _connect() as conn, conn.cursor() as cur:
        # Single atomic, status-guarded UPDATE. Because the WHERE clause only
        # matches a still-PENDING order, concurrent/retried webhook calls can
        # never transition the same order twice — no explicit row lock needed
        # (which would not hold under autocommit anyway).
        cur.execute(
            f"""
            UPDATE {SCHEMA}.orders
            SET status = %s,
                provider_payment_id = COALESCE(%s, provider_payment_id),
                updated_at = now(){paid_clause}
            WHERE order_id = %s AND status = %s
            RETURNING thread_id
            """,
            (status, provider_payment_id, order_id, STATUS_PENDING),
        )
        row = cur.fetchone()
        if row:
            # Only a successful PENDING -> PAID transition clears the cart, so a
            # failed/canceled payment leaves the cart intact for retry, and a
            # duplicate webhook (already terminal, no row returned) can't clear
            # a cart that was already rebuilt for a new order.
            if status == STATUS_PAID:
                cur.execute(
                    f"DELETE FROM {SCHEMA}.cart_items WHERE thread_id = %s",
                    (row["thread_id"],),
                )
            return True, status

        # No row transitioned: either the order is missing or already terminal.
        cur.execute(
            f"SELECT status FROM {SCHEMA}.orders WHERE order_id = %s", (order_id,)
        )
        existing = cur.fetchone()
        if not existing:
            return False, "NOT_FOUND"
        return False, existing["status"]
