"""
Persist the evidence (images/tables) shown with each chat answer.

Evidence is streamed to the frontend after the answer, but LangGraph's
checkpointer only stores the answer text, so pictures used to disappear when
a chat was reloaded. This module keeps a small `public.chat_message_evidence`
table keyed by (thread_id, message_id) so the history endpoint can return the
same evidence with each AI message.

All functions are defensive: a storage failure must never break the chat flow.
"""

import json
import logging

import psycopg

from core.config import settings

logger = logging.getLogger(__name__)

_table_created = False


def ensure_evidence_table() -> None:
    """Create the chat_message_evidence table if it doesn't exist (idempotent)."""
    global _table_created
    if _table_created:
        return

    try:
        with psycopg.connect(settings.POSTGRES_URL, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS public.chat_message_evidence (
                        thread_id  VARCHAR(255) NOT NULL,
                        message_id VARCHAR(255) NOT NULL,
                        items      JSONB        NOT NULL,
                        created_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
                        PRIMARY KEY (thread_id, message_id)
                    )
                """)
        _table_created = True
    except Exception as e:
        logger.warning(f"chat_message_evidence table creation check failed: {e}")


def save_message_evidence(thread_id: str, message_id: str, items: list[dict]) -> None:
    """Store the evidence shown with one AI message. Never raises."""
    if not thread_id or not message_id or not items:
        return

    ensure_evidence_table()

    try:
        with psycopg.connect(settings.POSTGRES_URL, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO public.chat_message_evidence (thread_id, message_id, items)
                    VALUES (%(thread_id)s, %(message_id)s, %(items)s::jsonb)
                    ON CONFLICT (thread_id, message_id)
                    DO UPDATE SET items = EXCLUDED.items
                """, {
                    "thread_id": thread_id,
                    "message_id": message_id,
                    "items": json.dumps(items, ensure_ascii=False),
                })
    except Exception as e:
        logger.warning(f"Failed to save evidence for {thread_id}/{message_id}: {e}")


def load_thread_evidence(thread_id: str) -> dict[str, list[dict]]:
    """Return {message_id: evidence items} for a thread. Empty on failure."""
    if not thread_id:
        return {}

    ensure_evidence_table()

    try:
        with psycopg.connect(settings.POSTGRES_URL, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT message_id, items
                    FROM public.chat_message_evidence
                    WHERE thread_id = %(thread_id)s
                """, {"thread_id": thread_id})
                rows = cur.fetchall()

        result: dict[str, list[dict]] = {}
        for message_id, items in rows:
            if isinstance(items, str):
                items = json.loads(items)
            if isinstance(items, list):
                result[message_id] = items
        return result
    except Exception as e:
        logger.warning(f"Failed to load evidence for {thread_id}: {e}")
        return {}
