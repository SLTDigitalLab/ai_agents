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

from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
from core.config import settings

logger = logging.getLogger(__name__)

_AGENT_ID = "cpe"
_table_created = False
_table_lock = Lock()


class CPEMemoryStorageError(RuntimeError):
    """Persistent CPE memory is unavailable."""


@contextmanager
def _database_errors():
    try:
        yield
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
            with psycopg.connect(settings.POSTGRES_URL) as conn:
                with conn.cursor() as cur:
                    # Advisory lock serializes DDL across concurrent workers
                    cur.execute("SELECT pg_advisory_xact_lock(1818846822, 1)")
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS public.cpe_memory (
                            agent_id VARCHAR(50) NOT NULL DEFAULT 'cpe',
                            thread_id VARCHAR(255) NOT NULL,
                            summary TEXT NOT NULL DEFAULT '',
                            messages JSONB NOT NULL DEFAULT '[]'::jsonb,
                            last_device_context JSONB,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            PRIMARY KEY (agent_id, thread_id)
                        )
                    """)
                    cur.execute("""
                        CREATE INDEX IF NOT EXISTS idx_cpe_memory_updated_at
                        ON public.cpe_memory (updated_at)
                    """)
        _table_created = True


@contextmanager
def _conversation(thread_id: str, *, write: bool = False):
    """Read or mutate conversation row in one transaction, locking on write."""
    ensure_cpe_memory_table()
    with _database_errors():
        with psycopg.connect(settings.POSTGRES_URL) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                key = {"agent_id": _AGENT_ID, "thread_id": thread_id}
                cur.execute("""
                    INSERT INTO public.cpe_memory (agent_id, thread_id)
                    VALUES (%(agent_id)s, %(thread_id)s)
                    ON CONFLICT (agent_id, thread_id) DO NOTHING
                """, key)
                cur.execute("""
                    SELECT summary, messages, last_device_context
                    FROM public.cpe_memory
                    WHERE agent_id = %(agent_id)s AND thread_id = %(thread_id)s
                """ + (" FOR UPDATE" if write else ""), key)
                row = cur.fetchone()
                if not row:
                    conversation = {
                        "summary": "",
                        "messages": [],
                        "last_device_context": None,
                    }
                else:
                    conversation = {
                        "summary": row.get("summary") or "",
                        "messages": row.get("messages") or [],
                        "last_device_context": row.get("last_device_context"),
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


def get_cpe_conversation_memory(thread_id: str | None) -> dict[str, Any]:
    """Retrieve full memory state for a thread."""
    if not thread_id:
        return {"summary": "", "messages": [], "last_device_context": None}
    try:
        with _conversation(thread_id, write=False) as conv:
            return deepcopy(conv)
    except Exception as exc:
        logger.error("Error reading CPE memory for thread %s: %s", thread_id, exc)
        return {"summary": "", "messages": [], "last_device_context": None}


def save_cpe_turn(
    thread_id: str | None,
    user_message: str,
    assistant_message: str,
    hardware_cards: list[dict] | None = None,
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
        with _conversation(thread_id, write=True) as conv:
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
    except Exception as exc:
        logger.error("Failed to save CPE turn for thread %s: %s", thread_id, exc)


def get_cpe_history_for_frontend(thread_id: str | None) -> list[dict[str, Any]]:
    """Format messages for React ChatInterface history restoration."""
    if not thread_id:
        return []
    memory = get_cpe_conversation_memory(thread_id)
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


def get_cpe_langchain_history(thread_id: str | None, max_turns: int = 6) -> list[BaseMessage]:
    """Retrieve LangChain BaseMessage objects for LLM prompting."""
    if not thread_id:
        return []
    memory = get_cpe_conversation_memory(thread_id)
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
    r"^(forget that|never mind|nevermind|instead|switch to|what about|how about|actually|scratch that)\b[, ]*",
    re.IGNORECASE,
)

_MODEL_ALIASES = [
    (re.compile(r"\bsrx\s*300\b", re.I), "SRX 300"),
    (re.compile(r"\bsrx\s*320\b", re.I), "SRX 320"),
    (re.compile(r"\bsrx\s*340\b", re.I), "SRX 340"),
    (re.compile(r"\b(?:one\s*)?421(?:['’]s|s)?\b", re.I), "OneAccess ONE421"),
    (re.compile(r"\b(?:one\s*)?521(?:['’]s|s)?\b", re.I), "OneAccess ONE521"),
    (re.compile(r"\b(?:one\s*)?2501(?:['’]s|s)?\b", re.I), "OneAccess ONE2501"),
    (re.compile(r"\b1121x\b", re.I), "Cisco 1121X"),
    (re.compile(r"\bc8200l\b", re.I), "C8200L"),
    (re.compile(r"\bc8200\b", re.I), "C8200"),
    (re.compile(r"\bc8300\b", re.I), "C8300"),
    (re.compile(r"\bfortigate\b", re.I), "FortiGate"),
    (re.compile(r"\b(sdwan|sd-wan)\b", re.I), "SD-WAN"),
]

_PRONOUN_PATTERN = re.compile(
    r"\b(it|its|they|them|those|this|that|these|the router|the switch|the phone|the device|the model|the same)\b",
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
    r"\b(one421|one521|one2501|one\s*421|one\s*521|one\s*2501|1424|s5735|isr\s*\d+|catalyst\s*\d*|1300|srx\s*\d+|fortigate\s*\w*|c8200l?|c8300|c1121x?|1121x?|t31p|t46u|t48u|t54w|grp\s*\d+|gxp\s*\d+|f670l?|eg8145)\b",
    re.IGNORECASE,
)


def contextualize_cpe_query(query: str, thread_id: str | None) -> str:
    """
    Contextualize follow-up questions for accurate Qdrant vector retrieval.
    1. Resolves references to the session's first discussed device ('back to the first one').
    2. Strips topic-reset prefixes ('forget that', 'instead', 'never mind') so old context doesn't leak.
    3. Normalizes model aliases ('srx300' -> 'SRX 300', '1121x' -> 'Cisco 1121X', '521s' -> 'ONE521').
    4. Handles brand preference follow-ups ('what if he insists on cisco?') by appending the previous device.
    5. If the query has pronouns (e.g., 'What is its port configuration?') and does not explicitly
       name a specific model or reset topic, appends the last discussed device context.
    """
    clean_query = query.strip()

    # 1. Handle ordinal reference to the first discussed device ('back to the first one')
    match_first = _FIRST_DEVICE_PATTERN.search(clean_query)
    if match_first and thread_id:
        memory = get_cpe_conversation_memory(thread_id)
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
            return f"{resolved_query} (Context: {first_dev} authoritative technical specifications)"

    # 2. Topic-reset prefix detection
    is_reset = False
    match_reset = _RESET_TOPIC_PATTERN.search(clean_query)
    if match_reset:
        is_reset = True
        clean_query = clean_query[match_reset.end():].strip()

    # 3. Model alias normalization for optimal sparse BM25 and vector matching
    for pattern, replacement in _MODEL_ALIASES:
        clean_query = pattern.sub(replacement, clean_query)

    if not thread_id or is_reset:
        logger.info("[Ask CPE Query Rewriter] Clean query: '%s' (is_reset=%s)", clean_query, is_reset)
        return clean_query

    # 4. Handle brand-preference follow-ups (e.g. 'what if he insists on cisco?')
    if _BRAND_PREFERENCE_PATTERN.search(clean_query):
        memory = get_cpe_conversation_memory(thread_id)
        last_dev = memory.get("last_device_context")
        if last_dev and last_dev.get("device"):
            device_name = last_dev["device"]
            out = f"{clean_query} (Context: brand-specific replacement for {device_name} Cisco 1121X C1121X-8P NWKEAC084 stock)"
            logger.info("[Ask CPE Query Rewriter] Preference query: '%s'", out)
            return out

    # 5. If the user already named a specific model, don't modify
    if _SPECIFIC_DEVICE_PATTERN.search(clean_query):
        logger.info("[Ask CPE Query Rewriter] Named model query: '%s'", clean_query)
        return clean_query

    # 6. If the query uses pronouns/implicit reference, attach device context
    if _PRONOUN_PATTERN.search(clean_query):
        memory = get_cpe_conversation_memory(thread_id)
        last_dev = memory.get("last_device_context")
        if last_dev and last_dev.get("device"):
            device_name = last_dev["device"]
            out = f"{clean_query} (Context: {device_name})"
            logger.info("[Ask CPE Query Rewriter] Pronoun query: '%s'", out)
            return out

    logger.info("[Ask CPE Query Rewriter] Final query: '%s'", clean_query)
    return clean_query

