"""
Session identity tracking.

LangGraph's checkpointer stores conversation *messages* keyed by thread_id but
keeps no readable record of *who* the user behind a session was. This module
maintains a lightweight `public.chat_sessions` table that maps each
(agent_id, thread_id) to the authenticated user (id + display name), so the
admin panel can show who used the system and personalization can address users
by name.

Writes happen on every chat turn (cheap upsert); reads happen from the admin
dashboard. All functions are defensive — a tracking failure must never break
the chat flow.
"""

import logging
from typing import Optional

import psycopg
from psycopg.rows import dict_row

from core.config import settings

logger = logging.getLogger(__name__)

_table_created = False


def ensure_sessions_table() -> None:
    """Create the chat_sessions table if it doesn't exist (idempotent)."""
    global _table_created
    if _table_created:
        return

    try:
        with psycopg.connect(settings.POSTGRES_URL, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS public.chat_sessions (
                        agent_id       VARCHAR(50)  NOT NULL,
                        thread_id      VARCHAR(255) NOT NULL,
                        user_id        VARCHAR(255) NOT NULL,
                        user_name      VARCHAR(255),
                        created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
                        last_active_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
                        PRIMARY KEY (agent_id, thread_id)
                    )
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_chat_sessions_user
                    ON public.chat_sessions (user_id)
                """)
        _table_created = True
    except Exception as e:
        logger.warning(f"chat_sessions table creation check failed: {e}")


def record_session(
    agent_id: str,
    thread_id: str,
    user_id: str,
    user_name: Optional[str] = None,
) -> None:
    """Upsert the user identity for a session and bump its last-active time.

    Safe to call on every chat turn. The display name is preserved when a later
    turn omits it (COALESCE), so we never overwrite a known name with NULL.
    """
    if not thread_id or not user_id:
        return

    ensure_sessions_table()

    try:
        with psycopg.connect(settings.POSTGRES_URL, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO public.chat_sessions
                        (agent_id, thread_id, user_id, user_name)
                    VALUES
                        (%(agent_id)s, %(thread_id)s, %(user_id)s, %(user_name)s)
                    ON CONFLICT (agent_id, thread_id)
                    DO UPDATE SET
                        user_id        = EXCLUDED.user_id,
                        user_name      = COALESCE(EXCLUDED.user_name, public.chat_sessions.user_name),
                        last_active_at = NOW()
                """, {
                    "agent_id": agent_id,
                    "thread_id": thread_id,
                    "user_id": user_id,
                    "user_name": user_name or None,
                })
    except Exception as e:
        logger.warning(f"Failed to record session {agent_id}/{thread_id}: {e}")


def get_sessions_users(agent_id: str, thread_ids: list[str]) -> dict[str, dict]:
    """Return a map of thread_id → {user_id, user_name, created_at, last_active_at}.

    Used to enrich the admin session list. Returns an empty map on failure.
    """
    if not thread_ids:
        return {}

    ensure_sessions_table()

    try:
        with psycopg.connect(settings.POSTGRES_URL, autocommit=True) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("""
                    SELECT thread_id, user_id, user_name, created_at, last_active_at
                    FROM public.chat_sessions
                    WHERE agent_id = %(agent_id)s
                      AND thread_id = ANY(%(thread_ids)s)
                """, {"agent_id": agent_id, "thread_ids": list(thread_ids)})
                rows = cur.fetchall()

        return {row["thread_id"]: row for row in rows}
    except Exception as e:
        logger.warning(f"Failed to load session users for {agent_id}: {e}")
        return {}
