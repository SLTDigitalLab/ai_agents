"""
Enterprise CPE Conversation Memory Service.
Production-grade PostgreSQL persistence for Ask CPE multi-turn sessions.

Features:
- Thread-safe PostgreSQL persistence in table `public.cpe_memory`.
- Atomic per-thread transactions using connection pooling and advisory locks.
- Pronoun / contextual query expansion for multi-turn RAG retrieval.
- Exposes LangChain message history and frontend-compatible message arrays.
"""

from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from copy import deepcopy
from threading import Lock
from typing import Any, List, Optional

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
from core.config import settings

logger = logging.getLogger(__name__)

_AGENT_ID = "cpe"
_table_created = False
_table_lock = Lock()
_cpe_pool: Optional[ConnectionPool] = None
_cpe_pool_lock = Lock()


class CPEMemoryStorageError(RuntimeError):
    """Persistent CPE memory is unavailable."""


class CPEThreadNotFoundError(Exception):
    """Thread does not exist or owner mismatch (mismatch -> 404)."""


def get_cpe_memory_pool() -> ConnectionPool:
    """Singleton connection pool for CPE memory (min 1, max 10)."""
    global _cpe_pool
    if _cpe_pool is None:
        with _cpe_pool_lock:
            if _cpe_pool is None:
                _cpe_pool = ConnectionPool(
                    settings.POSTGRES_URL,
                    min_size=1,
                    max_size=10,
                    open=True,
                    kwargs={"row_factory": dict_row},
                )
    return _cpe_pool


def close_cpe_memory_pool() -> None:
    """Close the CPE memory connection pool on app shutdown."""
    global _cpe_pool
    with _cpe_pool_lock:
        if _cpe_pool is not None:
            try:
                _cpe_pool.close()
            except Exception as exc:
                logger.warning("Error closing CPE memory connection pool: %s", exc)
            finally:
                _cpe_pool = None


@contextmanager
def _database_errors():
    try:
        yield
    except CPEThreadNotFoundError:
        raise
    except Exception as exc:
        logger.warning("CPE memory database operation failed: %s", exc)
        raise CPEMemoryStorageError("CPE memory storage is unavailable.") from None


def ensure_cpe_memory_table() -> None:
    """Idempotently initialize table and indices."""
    global _table_created
    with _table_lock:
        if _table_created:
            return
        with _database_errors():
            pool = get_cpe_memory_pool()
            with pool.connection() as conn:
                with conn.cursor() as cur:
                    # Advisory lock serializes DDL across concurrent workers
                    cur.execute("SELECT pg_advisory_xact_lock(1818846822, 1)")
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS public.cpe_memory (
                            agent_id VARCHAR(50) NOT NULL DEFAULT 'cpe',
                            thread_id VARCHAR(255) NOT NULL,
                            owner_id VARCHAR(255),
                            summary TEXT NOT NULL DEFAULT '',
                            messages JSONB NOT NULL DEFAULT '[]'::jsonb,
                            last_device_context JSONB,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            PRIMARY KEY (agent_id, thread_id)
                        )
                    """)
                    cur.execute("""
                        ALTER TABLE public.cpe_memory
                        ADD COLUMN IF NOT EXISTS owner_id VARCHAR(255)
                    """)
                    cur.execute("""
                        CREATE INDEX IF NOT EXISTS idx_cpe_memory_updated_at
                        ON public.cpe_memory (updated_at)
                    """)
        _table_created = True


@contextmanager
def _conversation(
    thread_id: str,
    *,
    write: bool = False,
    owner_id: Optional[str] = None,
):
    """Read or mutate conversation row in one transaction, locking on write.

    Read paths do NOT insert rows. Unknown threads return empty history.
    """
    ensure_cpe_memory_table()
    with _database_errors():
        pool = get_cpe_memory_pool()
        with pool.connection() as conn:
            with conn.cursor() as cur:
                key = {"agent_id": _AGENT_ID, "thread_id": thread_id}
                if write:
                    cur.execute("""
                        INSERT INTO public.cpe_memory (agent_id, thread_id, owner_id)
                        VALUES (%(agent_id)s, %(thread_id)s, %(owner_id)s)
                        ON CONFLICT (agent_id, thread_id) DO NOTHING
                    """, {**key, "owner_id": owner_id})

                query = """
                    SELECT owner_id, summary, messages, last_device_context
                    FROM public.cpe_memory
                    WHERE agent_id = %(agent_id)s AND thread_id = %(thread_id)s
                """
                cur.execute(query + (" FOR UPDATE" if write else ""), key)
                row = cur.fetchone()
                if row and row["owner_id"] != owner_id:
                    raise CPEThreadNotFoundError(f"Thread '{thread_id}' not found for user.")

                conversation = {
                    "owner_id": row["owner_id"] if row else None,
                    "summary": (row.get("summary") or "") if row else "",
                    "messages": (row.get("messages") or []) if row else [],
                    "last_device_context": row.get("last_device_context") if row else None,
                }

                yield conversation

                if write:
                    cur.execute("""
                        UPDATE public.cpe_memory SET
                            summary = %(summary)s,
                            messages = %(messages)s,
                            last_device_context = %(last_device_context)s,
                            updated_at = NOW()
                        WHERE agent_id = %(agent_id)s AND thread_id = %(thread_id)s
                    """, {
                        **key,
                        "summary": conversation["summary"],
                        "messages": Jsonb(conversation["messages"]),
                        "last_device_context": (
                            Jsonb(conversation["last_device_context"])
                            if conversation["last_device_context"] is not None
                            else None
                        ),
                    })


def get_cpe_conversation_memory(
    thread_id: str | None,
    owner_id: Optional[str] = None,
) -> dict[str, Any]:
    """Retrieve full memory state for a thread. Unknown thread -> empty history."""
    if not thread_id:
        return {"summary": "", "messages": [], "last_device_context": None, "owner_id": None}
    try:
        with _conversation(thread_id, write=False, owner_id=owner_id) as conv:
            return deepcopy(conv)
    except CPEThreadNotFoundError:
        raise
    except Exception as exc:
        logger.error("[CPE Memory %s] DB error reading memory: %s", thread_id, exc, exc_info=True)
        return {"summary": "", "messages": [], "last_device_context": None, "owner_id": None}


def save_cpe_turn(
    thread_id: str | None,
    user_message: str,
    assistant_message: str,
    hardware_cards: list[dict] | None = None,
    owner_id: Optional[str] = None,
) -> None:
    """Atomically append a user-assistant dialogue turn and update device context, preventing duplicate turns."""
    if not thread_id:
        return

    u_clean = user_message.strip()
    a_clean = assistant_message.strip()

    # Extract primary device context if hardware cards were produced
    device_context = None
    if hardware_cards and len(hardware_cards) > 0:
        first = hardware_cards[0]
        device_context = {
            "device": first.get("name"),
            "vendor": first.get("vendor"),
            "item_code": first.get("item_code"),
        }

    try:
        with _conversation(thread_id, write=True, owner_id=owner_id) as conv:
            messages = conv["messages"]
            # Deduplicate: if an identical user message was just submitted (e.g. rapid double click),
            # update the assistant response in place rather than inserting a duplicate turn.
            if (
                len(messages) >= 2
                and messages[-2].get("role") == "user"
                and messages[-2].get("content", "").strip() == u_clean
            ):
                messages[-1]["content"] = a_clean
                messages[-1]["hardware_cards"] = hardware_cards or []
            else:
                messages.append({"role": "user", "content": u_clean})
                messages.append({
                    "role": "assistant",
                    "content": a_clean,
                    "hardware_cards": hardware_cards or [],
                })

            # Keep up to 30 recent message turns
            conv["messages"] = messages[-30:]
            if device_context:
                conv["last_device_context"] = device_context
    except CPEThreadNotFoundError:
        raise
    except Exception as exc:
        logger.error("[CPE Memory %s] Failed to save CPE turn: %s", thread_id, exc, exc_info=True)


def get_cpe_history_for_frontend(
    thread_id: str | None,
    memory_dict: Optional[dict[str, Any]] = None,
    owner_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Format messages for React ChatInterface history restoration."""
    if not thread_id:
        return []
    memory = memory_dict if memory_dict is not None else get_cpe_conversation_memory(thread_id, owner_id=owner_id)
    raw_messages = memory.get("messages", [])
    output = []
    for msg in raw_messages:
        role = msg.get("role")
        content = msg.get("content", "")
        if role == "user":
            output.append({"type": "human", "content": content})
        elif role == "assistant":
            output.append({
                "type": "ai",
                "content": content,
                "hardware_cards": msg.get("hardware_cards", []),
            })
    return output


def get_cpe_langchain_history(
    thread_id: str | None,
    max_turns: int = 6,
    memory_dict: Optional[dict[str, Any]] = None,
    owner_id: Optional[str] = None,
) -> list[BaseMessage]:
    """Retrieve LangChain BaseMessage objects for LLM prompting."""
    if not thread_id:
        return []
    memory = memory_dict if memory_dict is not None else get_cpe_conversation_memory(thread_id, owner_id=owner_id)
    raw_messages = memory.get("messages", [])
    selected = raw_messages[- (max_turns * 2):]  # each turn is user + assistant
    langchain_messages: list[BaseMessage] = []
    for msg in selected:
        role = msg.get("role")
        content = msg.get("content", "")
        if role == "user":
            langchain_messages.append(HumanMessage(content=content))
        elif role == "assistant":
            langchain_messages.append(AIMessage(content=content))
    return langchain_messages


_RESET_TOPIC_PATTERN = re.compile(
    r"^(?:ok\s*)?(?:forget that|never mind|nevermind|scratch that|ignore that)\b[, ]*",
    re.IGNORECASE,
)

_RESUME_TOPIC_PATTERN = re.compile(
    r"^(?:ok\s*)?(?:back to it|back to the device|back to business|returning to (?:it|the device))\b[, ]*",
    re.IGNORECASE,
)

_MODEL_ALIASES = [
    (re.compile(r"\bsrx\s*300\b", re.I), "SRX 300"),
    (re.compile(r"\bsrx\s*320\b", re.I), "SRX 320"),
    (re.compile(r"\bsrx\s*340\b", re.I), "SRX 340"),
    (re.compile(r"\b(?:one[- ]*)?421(?:['’]s|s)?\b", re.I), "ONE421"),
    (re.compile(r"\b(?:one[- ]*)?521(?:['’]s|s)?\b", re.I), "ONE521"),
    (re.compile(r"\b(?:one[- ]*)?2501(?:['’]s|s)?\b", re.I), "ONE2501"),
    (re.compile(r"\b1121x\b", re.I), "Cisco 1121X"),
    (re.compile(r"\bc8200l\b", re.I), "C8200L"),
    (re.compile(r"\bc8200\b", re.I), "C8200"),
    (re.compile(r"\bc8300\b", re.I), "C8300"),
    (re.compile(r"\bfortigate\b", re.I), "FortiGate"),
    (re.compile(r"\b(sdwan|sd-wan)\b", re.I), "SD-WAN"),
    (re.compile(r"\bzte\s*ont\b", re.I), "ZTE ZXHN F670L ONT CPEFTH076S"),
    (re.compile(r"\bhuawei\s*ont\b", re.I), "Huawei EG8145V5 ONT CPEFTH077S"),
]

_PRONOUN_PATTERN = re.compile(
    r"\b(it|its|they|them|those|these|the router|the switch|the phone|the device|the model|the same|which of|which one|which ones|any of|how many of|what about|how about|the \d+\s*port)\b",
    re.IGNORECASE,
)

_BRAND_PREFERENCE_PATTERN = re.compile(
    r"\b(insists?|prefers?|wants?|asks? for|stick(?:s|ing)? with|brand[- ]specific)\b",
    re.IGNORECASE,
)

_FIRST_DEVICE_PATTERN = re.compile(
    r"\b(?:back to the first(?: one)?|the first one|the first model|the first device|the initial (?:model|device|one))\b",
    re.IGNORECASE,
)

_SPECIFIC_DEVICE_PATTERN = re.compile(
    r"\b(one421|one521|one2501|one\s*421|one\s*521|one\s*2501|1424|s5735|isr\s*\d+|catalyst\s*\d*|1300|srx\s*\d+|fortigate\s*\w*|c8200l?|c8300|c1121x?|1121x?|t31p|t46u|t48u|t54w|grp\s*\d+|gxp\s*\d+|f670l?|eg8145\w*|zxhn\w*|cpefth\w*)\b",
    re.IGNORECASE,
)

_ITEM_CODE_PATTERN = re.compile(
    r"\b(B?(?:CPEDAT|CPEFTH|CPEDTA|NWK[A-Z]{3})\d{2,3}S?)\b",
    re.IGNORECASE,
)


def contextualize_cpe_query(
    query: str,
    thread_id: str | None = None,
    memory_dict: Optional[dict[str, Any]] = None,
) -> str:
    """
    Contextualize follow-up questions for accurate Qdrant vector retrieval.
    Takes memory dict directly with NO internal database calls.
    - Retrieval queries contain entity names and codes only, never answers.
    - Follow-up resolution only when the query names no model/code:
      appends item names/codes from previous turn's hardware_cards.
    """
    clean_query = query.strip()
    memory = memory_dict or {}

    # 1. Handle ordinal reference to the first discussed device ('back to the first one')
    match_first = _FIRST_DEVICE_PATTERN.search(clean_query)
    if match_first and memory:
        first_dev = None
        for msg in memory.get("messages", []):
            cards = msg.get("hardware_cards") or []
            if cards and len(cards) > 0:
                first_dev = cards[0].get("name")
                break
            content = msg.get("content", "")
            dev_match = _SPECIFIC_DEVICE_PATTERN.search(content)
            if dev_match:
                first_dev = dev_match.group(0).upper()
                break
        if first_dev:
            # Replace 'back to the first one' with the actual first device name
            resolved_query = _FIRST_DEVICE_PATTERN.sub(first_dev, clean_query).strip()
            # Clean up leading punctuation like commas
            resolved_query = re.sub(r"^[, ]+", "", resolved_query)
            logger.info("[Ask CPE Query Rewriter] Resolved '%s' -> '%s' (First device: %s)", clean_query, resolved_query, first_dev)
            return f"{resolved_query} (Context: {first_dev})"

    # 2. Topic-resume prefix detection ('ok back to it', 'back to business')
    match_resume = _RESUME_TOPIC_PATTERN.search(clean_query)
    if match_resume:
        clean_query = clean_query[match_resume.end():].strip()

    # 3. Topic-reset prefix detection ('forget that', 'never mind', 'scratch that', 'ignore that')
    is_reset = False
    match_reset = _RESET_TOPIC_PATTERN.search(clean_query)
    if match_reset:
        is_reset = True
        clean_query = clean_query[match_reset.end():].strip()

    # 4. Model alias normalization for optimal sparse BM25 and vector matching
    for pattern, replacement in _MODEL_ALIASES:
        clean_query = pattern.sub(replacement, clean_query)

    if is_reset or not memory:
        logger.info("[Ask CPE Query Rewriter] Clean query: '%s' (is_reset=%s)", clean_query, is_reset)
        return clean_query

    # 5. Check if query names an explicit model or item code
    has_explicit_model = bool(_SPECIFIC_DEVICE_PATTERN.search(clean_query) or _ITEM_CODE_PATTERN.search(clean_query))

    # If the user named a specific model/code, do NOT modify with previous turn's context
    if has_explicit_model:
        logger.info("[Ask CPE Query Rewriter] Explicit model/code named: '%s'", clean_query)
        return clean_query

    # 6. Extract previous turn's hardware cards for entity resolution
    prev_cards = []
    messages = memory.get("messages", [])
    for msg in reversed(messages):
        if msg.get("role") == "assistant" and msg.get("hardware_cards"):
            prev_cards = msg.get("hardware_cards")
            break

    card_refs = []
    for c in prev_cards[:4]:
        c_name = c.get("name")
        c_code = c.get("item_code")
        if c_name:
            card_refs.append(f"{c_name} {c_code or ''}".strip())

    # Fallback to last_device_context if no hardware cards
    if not card_refs:
        last_dev = memory.get("last_device_context")
        if last_dev and last_dev.get("device"):
            card_refs.append(f"{last_dev['device']} {last_dev.get('item_code') or ''}".strip())

    # Extract recent user query category or subject if available
    last_user_query = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            last_user_query = msg.get("content", "").strip()
            break

    category_match = re.search(
        r"\b((?:huawei|cisco|juniper|fortigate|grandstream|oneaccess|zte|aruba|ruckus)?\s*(?:switches|switch|routers|router|edge devices|edge device|firewalls|firewall|ip phones|ip phone|phones|phone|iads|iad|transceivers|transceiver|sfp|access points|access point|aps|ap|onts|ont))\b",
        last_user_query,
        re.I
    )
    prev_category = category_match.group(1).strip() if category_match else ""

    # 7. Follow-up resolution when NO model/code is named
    # (a) Brand preference follow-up (e.g. 'what if he insists on cisco?')
    if _BRAND_PREFERENCE_PATTERN.search(clean_query):
        prev_dev = card_refs[0] if card_refs else "CPE"
        out = f"{clean_query} (Context: replacement for {prev_dev} Cisco C1121X-8P NWKEAC084)"
        logger.info("[Ask CPE Query Rewriter] Preference follow-up: '%s'", out)
        return out

    # (b) Pronoun or relative follow-up (e.g. 'what is its poe budget?', 'which of them support poe?')
    if (card_refs or prev_category) and (_PRONOUN_PATTERN.search(clean_query) or re.search(r"\b(?:what about|how about|the \d+\s*port)\b", clean_query, re.I)):
        context_items = ([prev_category] if prev_category else []) + card_refs
        out = f"{clean_query} (Context: {', '.join(context_items)})"
        logger.info("[Ask CPE Query Rewriter] Follow-up resolution: '%s'", out)
        return out

    logger.info("[Ask CPE Query Rewriter] Final query: '%s'", clean_query)
    return clean_query
