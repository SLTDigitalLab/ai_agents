from __future__ import annotations

import logging
from contextlib import contextmanager
from copy import deepcopy
from threading import Lock
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from core.config import settings

# Canonical ID used by LifeStoreMCPChatRequest.
_AGENT_ID = "ask_lifestore"
_table_created = False
_table_lock = Lock()
logger = logging.getLogger(__name__)


class MemoryStorageError(RuntimeError):
    """Persistent memory is unavailable; callers must not substitute local state."""


@contextmanager
def _database_errors():
    try:
        yield
    except Exception:
        # The router includes exception text/traceback in its response. Suppress
        # the original exception chain so connection credentials cannot escape.
        logger.warning("LifeStore memory database operation failed")
        raise MemoryStorageError("LifeStore memory storage is unavailable.") from None


def ensure_memory_table() -> None:
    """Idempotent lazy initialization; only the DDL success flag is cached."""
    global _table_created
    with _table_lock:
        if _table_created:
            return
        with _database_errors():
            with psycopg.connect(settings.POSTGRES_URL) as conn:
                with conn.cursor() as cur:
                    # Serialize first-time DDL across backend workers as well as
                    # threads. This lock is released on commit or rollback.
                    cur.execute("SELECT pg_advisory_xact_lock(1818846821, 1)")
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS public.lifestore_memory (
                            agent_id VARCHAR(50) NOT NULL,
                            thread_id VARCHAR(255) NOT NULL,
                            summary TEXT NOT NULL DEFAULT '',
                            messages JSONB NOT NULL DEFAULT '[]'::jsonb,
                            current_category TEXT,
                            last_products_shown JSONB NOT NULL DEFAULT '[]'::jsonb,
                            last_selected_product JSONB,
                            pending_action TEXT,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            PRIMARY KEY (agent_id, thread_id)
                        )
                    """)
                    cur.execute("""
                        CREATE INDEX IF NOT EXISTS idx_lifestore_memory_updated_at
                        ON public.lifestore_memory (updated_at)
                    """)
        _table_created = True


@contextmanager
def _conversation(thread_id: str, *, write: bool = False):
    """Read or mutate a row in one transaction, serializing writers per thread."""
    ensure_memory_table()
    with _database_errors():
        with psycopg.connect(settings.POSTGRES_URL) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                key = {"agent_id": _AGENT_ID, "thread_id": thread_id}
                cur.execute("""
                    INSERT INTO public.lifestore_memory (agent_id, thread_id)
                    VALUES (%(agent_id)s, %(thread_id)s)
                    ON CONFLICT (agent_id, thread_id) DO NOTHING
                """, key)
                cur.execute("""
                    SELECT summary, messages, current_category,
                           last_products_shown, last_selected_product, pending_action
                    FROM public.lifestore_memory
                    WHERE agent_id = %(agent_id)s AND thread_id = %(thread_id)s
                """ + (" FOR UPDATE" if write else ""), key)
                row = cur.fetchone()
                conversation = {
                    "summary": row["summary"], "messages": row["messages"],
                    "state": {name: row[name] for name in (
                        "current_category", "last_products_shown",
                        "last_selected_product", "pending_action")},
                }
                yield conversation
                if write:
                    state = conversation["state"]
                    cur.execute("""
                        UPDATE public.lifestore_memory SET
                            summary = %(summary)s, messages = %(messages)s,
                            current_category = %(current_category)s,
                            last_products_shown = %(last_products_shown)s,
                            last_selected_product = %(last_selected_product)s,
                            pending_action = %(pending_action)s, updated_at = NOW()
                        WHERE agent_id = %(agent_id)s AND thread_id = %(thread_id)s
                    """, {**key, **state, "summary": conversation["summary"],
                          "messages": Jsonb(conversation["messages"]),
                          "last_products_shown": Jsonb(state["last_products_shown"]),
                          "last_selected_product": (Jsonb(state["last_selected_product"])
                              if state["last_selected_product"] is not None else None)})


def _empty_conversation() -> dict[str, Any]:
    """
    Create the default memory structure for a new conversation.
    """

    return {
        "summary": "",
        "messages": [],
        "state": {
            "current_category": None,
            "last_products_shown": [],
            "last_selected_product": None,
            "pending_action": None,
        },
    }


# ============================================================
# GET MEMORY
# ============================================================

def get_conversation_memory(
    thread_id: str | None,
) -> dict[str, Any]:
    """
    Return a COPY of the complete memory for a conversation.

    Returning a copy is important because code outside this
    service should not accidentally modify the stored memory.
    """

    if not thread_id:
        return _empty_conversation()

    with _conversation(thread_id, write=False) as conversation:

        return deepcopy(conversation)


# ============================================================
# MESSAGE MEMORY
# ============================================================

def save_message(
    thread_id: str | None,
    role: str,
    content: str,
) -> None:
    """
    Save a user or assistant message.

    For now we keep the latest 20 messages.

    Later, when summarization is added, older messages will
    be summarized instead of simply disappearing.
    """

    if not thread_id:
        return

    with _conversation(thread_id, write=True) as conversation:

        conversation["messages"].append(
            {
                "role": role,
                "content": content,
            }
        )

        # Temporary limit.
        # Stage 2 will replace this with summarization.
        conversation["messages"] = conversation["messages"][-20:]


# ============================================================
# STRUCTURED PRODUCT MEMORY
# ============================================================

def save_products_shown(
    thread_id: str | None,
    products: list[dict[str, Any]],
) -> None:
    """
    Remember the most recent products shown to the user.

    We deliberately store only small reference information.
    We do NOT store the entire product document.

    Current price/stock should still come from LifeStore
    retrieval when needed.
    """

    if not thread_id:
        return

    with _conversation(thread_id, write=True) as conversation:

        compact_products: list[dict[str, Any]] = []

        for product in products[:8]:

            if not isinstance(product, dict):
                continue

            compact_products.append(
                {
                    "product_id": product.get("product_id"),
                    "url": product.get("url"),
                    "name": product.get("name"),
                    "category": product.get("category"),
                    "product_type": product.get("product_type"),
                    "price": product.get("price"),
                    "price_value": product.get("price_value"),
                }
            )

        if len(compact_products) > 1:
            conversation["state"]["last_products_shown"] = compact_products
            # A new list becomes the active context; discard a stale single selection.
            conversation["state"]["last_selected_product"] = None
        elif len(compact_products) == 1:
            conversation["state"]["last_selected_product"] = compact_products[0]


def set_current_category(
    thread_id: str | None,
    category: str | None,
) -> None:
    """
    Remember the category currently being discussed.

    Example:
        router
        speaker
        laptop
    """

    if not thread_id:
        return

    if not category:
        return

    with _conversation(thread_id, write=True) as conversation:

        conversation["state"]["current_category"] = category


def set_last_selected_product(
    thread_id: str | None,
    product: dict[str, Any] | None,
) -> None:
    """
    Remember one specific product the user is currently
    discussing.

    Example:
        Prolink PRS1140 ADSL Router
    """

    if not thread_id:
        return

    with _conversation(thread_id, write=True) as conversation:

        if not product:
            conversation["state"]["last_selected_product"] = None
            return

        conversation["state"]["last_selected_product"] = {
            "product_id": product.get("product_id"),
            "url": product.get("url"),
            "name": product.get("name"),
            "category": product.get("category"),
            "product_type": product.get("product_type"),
            "price": product.get("price"),
            "price_value": product.get("price_value"),
        }


def set_pending_action(
    thread_id: str | None,
    action: str | None,
) -> None:
    """
    Remember an unfinished action.

    We are not using this heavily yet, but later this can store:

        purchase
        order_status
        cancellation
        confirmation
    """

    if not thread_id:
        return

    with _conversation(thread_id, write=True) as conversation:

        conversation["state"]["pending_action"] = action


# ============================================================
# SUMMARY
# ============================================================

def get_summary(
    thread_id: str | None,
) -> str:
    """
    Return the existing conversation summary.

    Stage 2 will add automatic LLM summarization.
    """

    if not thread_id:
        return ""

    with _conversation(thread_id, write=False) as conversation:

        return str(conversation.get("summary") or "")


def set_summary(
    thread_id: str | None,
    summary: str,
) -> None:
    """
    Save/update conversation summary.

    Stage 2 will call this automatically after the
    conversation becomes long.
    """

    if not thread_id:
        return

    with _conversation(thread_id, write=True) as conversation:

        conversation["summary"] = summary.strip()


# ============================================================
# DEBUG / TEST
# ============================================================

def get_summarization_payload(
    thread_id: str | None,
    trigger_count: int = 14,
    keep_recent: int = 6,
) -> dict[str, Any] | None:
    """
    Return the old messages that should be summarized.

    We only summarize when the number of exact messages becomes larger
    than trigger_count.

    Structured state is NOT touched here.
    """
    if not thread_id:
        return None

    with _conversation(thread_id, write=False) as conversation:
        messages = conversation.get("messages") or []

        if len(messages) <= trigger_count:
            return None

        # Example:
        # 16 messages total
        # keep_recent = 6
        #
        # first 10 -> summarize
        # last 6   -> keep exactly
        messages_to_summarize = messages[:-keep_recent]
        recent_messages = messages[-keep_recent:]

        if not messages_to_summarize:
            return None

        return {
            "existing_summary": str(
                conversation.get("summary") or ""
            ),
            "messages_to_summarize": deepcopy(
                messages_to_summarize
            ),
            "recent_messages": deepcopy(
                recent_messages
            ),
        }


def apply_conversation_summary(
    thread_id: str | None,
    summary: str,
    recent_messages: list[dict[str, str]],
) -> None:
    """
    Save the new running summary and replace old exact messages
    with only the recent messages.

    IMPORTANT:
    conversation["state"] is NOT changed.
    """
    if not thread_id:
        return

    cleaned_summary = str(summary or "").strip()

    if not cleaned_summary:
        return

    with _conversation(thread_id, write=True) as conversation:
        # Summarization runs outside this transaction. Keep messages appended
        # while the LLM was working instead of overwriting them with its snapshot.
        messages = conversation["messages"]
        retained = deepcopy(recent_messages)
        if messages and recent_messages:
            width = len(recent_messages)
            matches = [i for i in range(len(messages) - width + 1)
                       if messages[i:i + width] == recent_messages]
            if not matches:
                # Another summary or retention has invalidated this snapshot.
                # Leave the running summary and messages intact for the next pass.
                return
            retained.extend(deepcopy(messages[matches[-1] + width:]))
        conversation["summary"] = cleaned_summary
        conversation["messages"] = retained

def clear_conversation(
    thread_id: str | None,
) -> None:
    """
    Remove one conversation.

    Useful while developing/testing.
    """

    if not thread_id:
        return

    ensure_memory_table()
    with _database_errors():
        with psycopg.connect(settings.POSTGRES_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    DELETE FROM public.lifestore_memory
                    WHERE agent_id = %(agent_id)s AND thread_id = %(thread_id)s
                """, {"agent_id": _AGENT_ID, "thread_id": thread_id})
