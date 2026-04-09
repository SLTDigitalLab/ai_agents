"""
Feedback router – handles thumbs-up / thumbs-down ratings on AI responses.

POST /api/v1/feedback                → submit or toggle feedback
GET  /api/v1/feedback/{agent}/{thread} → get feedback for a conversation
GET  /api/v1/admin/dashboard/feedback  → aggregate stats for admin panel
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
import psycopg
from psycopg.rows import dict_row

from core.config import settings
from core.checkpointer import get_postgres_checkpointer
from domain.registry import AGENT_BUILDERS, get_agent_builder
from schemas.feedback import FeedbackRequest, FeedbackResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Feedback"])

VALID_AGENTS = set(AGENT_BUILDERS.keys())

# ── Table Setup ──────────────────────────────────────────────────────────

_table_created = False


def _ensure_feedback_table():
    """Create the feedback table in the public schema if it doesn't exist."""
    global _table_created
    if _table_created:
        return

    try:
        with psycopg.connect(settings.POSTGRES_URL, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS public.feedback (
                        id          SERIAL PRIMARY KEY,
                        agent_id    VARCHAR(50)  NOT NULL,
                        thread_id   VARCHAR(255) NOT NULL,
                        message_index INTEGER    NOT NULL,
                        rating      VARCHAR(10)  NOT NULL CHECK (rating IN ('up', 'down')),
                        user_id     VARCHAR(255) NOT NULL,
                        created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
                        updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
                        UNIQUE (agent_id, thread_id, message_index, user_id)
                    )
                """)
        _table_created = True
    except Exception as e:
        logger.warning(f"Feedback table creation check failed: {e}")


def _load_session_messages(agent_id: str, thread_id: str) -> list:
    """Load conversation messages for a session from LangGraph checkpoints.

    Returns a list of {"type": "human"|"ai", "content": str} dicts.
    """
    try:
        builder_fn = get_agent_builder(agent_id)
        workflow = builder_fn()
        config = {"configurable": {"thread_id": thread_id}}

        with get_postgres_checkpointer(agent_id) as checkpointer:
            graph = workflow.compile(checkpointer=checkpointer)
            snapshot = graph.get_state(config)

            if not snapshot.values:
                return []

            messages = []
            for msg in snapshot.values.get("messages", []):
                if msg.type not in ("human", "ai"):
                    continue
                content = msg.content
                if isinstance(content, list):
                    parts = []
                    for block in content:
                        if isinstance(block, str):
                            parts.append(block)
                        elif isinstance(block, dict) and "text" in block:
                            parts.append(block["text"])
                    content = " ".join(parts).strip()
                elif not isinstance(content, str):
                    content = str(content).strip()
                if content:
                    messages.append({"type": msg.type, "content": content})
            return messages
    except Exception as e:
        logger.warning(f"Failed to load session {agent_id}/{thread_id}: {e}")
        return []


# ── Submit / Toggle Feedback ──────────────────────────────────────────────

@router.post("/api/v1/feedback", response_model=FeedbackResponse)
async def submit_feedback(req: FeedbackRequest):
    """Submit or update feedback for an AI message.

    Uses upsert: if the user already rated this message, the rating is updated.
    """
    if req.agent_id not in VALID_AGENTS:
        raise HTTPException(status_code=404, detail=f"Unknown agent '{req.agent_id}'")

    _ensure_feedback_table()

    try:
        with psycopg.connect(settings.POSTGRES_URL, autocommit=True) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("""
                    INSERT INTO public.feedback (agent_id, thread_id, message_index, rating, user_id)
                    VALUES (%(agent_id)s, %(thread_id)s, %(message_index)s, %(rating)s, %(user_id)s)
                    ON CONFLICT (agent_id, thread_id, message_index, user_id)
                    DO UPDATE SET rating = %(rating)s, updated_at = NOW()
                    RETURNING id, agent_id, thread_id, message_index, rating, user_id
                """, {
                    "agent_id": req.agent_id,
                    "thread_id": req.thread_id,
                    "message_index": req.message_index,
                    "rating": req.rating,
                    "user_id": req.user_id,
                })
                row = cur.fetchone()

        return FeedbackResponse(**row)

    except Exception as exc:
        logger.error(f"Failed to submit feedback: {exc}")
        raise HTTPException(status_code=500, detail=f"Database error: {exc}")


# ── Delete Feedback ───────────────────────────────────────────────────────

@router.delete("/api/v1/feedback")
async def delete_feedback(req: FeedbackRequest):
    """Delete a user's feedback for a specific AI message."""
    if req.agent_id not in VALID_AGENTS:
        raise HTTPException(status_code=404, detail=f"Unknown agent '{req.agent_id}'")

    _ensure_feedback_table()

    try:
        with psycopg.connect(settings.POSTGRES_URL, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    DELETE FROM public.feedback
                    WHERE agent_id = %(agent_id)s
                      AND thread_id = %(thread_id)s
                      AND message_index = %(message_index)s
                      AND user_id = %(user_id)s
                """, {
                    "agent_id": req.agent_id,
                    "thread_id": req.thread_id,
                    "message_index": req.message_index,
                    "user_id": req.user_id,
                })

        return {"status": "deleted"}

    except Exception as exc:
        logger.error(f"Failed to delete feedback: {exc}")
        raise HTTPException(status_code=500, detail=f"Database error: {exc}")


# ── Get Feedback for a Conversation ───────────────────────────────────────

@router.get("/api/v1/feedback/{agent_id}/{thread_id}")
async def get_thread_feedback(agent_id: str, thread_id: str):
    """Return all feedback entries for a specific conversation thread.

    Returns a dict mapping message_index → rating for easy frontend lookup.
    """
    if agent_id not in VALID_AGENTS:
        raise HTTPException(status_code=404, detail=f"Unknown agent '{agent_id}'")

    _ensure_feedback_table()

    try:
        with psycopg.connect(settings.POSTGRES_URL, autocommit=True) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("""
                    SELECT message_index, rating, user_id
                    FROM public.feedback
                    WHERE agent_id = %(agent_id)s AND thread_id = %(thread_id)s
                    ORDER BY message_index
                """, {"agent_id": agent_id, "thread_id": thread_id})
                rows = cur.fetchall()

        # Build a lookup: { message_index: { user_id: rating } }
        feedback_map = {}
        for row in rows:
            idx = row["message_index"]
            if idx not in feedback_map:
                feedback_map[idx] = {}
            feedback_map[idx][row["user_id"]] = row["rating"]

        return {"feedback": feedback_map}

    except Exception as exc:
        logger.error(f"Failed to get feedback: {exc}")
        raise HTTPException(status_code=500, detail=f"Database error: {exc}")


# ── Admin: Feedback Stats ────────────────────────────────────────────────

@router.get("/api/v1/admin/dashboard/feedback")
async def get_feedback_stats(
    agent: Optional[str] = Query(None, description="Filter by agent ID"),
):
    """Return aggregate feedback statistics for the admin dashboard."""
    _ensure_feedback_table()

    try:
        with psycopg.connect(settings.POSTGRES_URL, autocommit=True) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                # Overall counts
                agent_filter = ""
                params = {}
                if agent and agent in VALID_AGENTS:
                    agent_filter = "WHERE agent_id = %(agent)s"
                    params["agent"] = agent

                cur.execute(f"""
                    SELECT
                        COUNT(*) as total_feedback,
                        COUNT(*) FILTER (WHERE rating = 'up') as thumbs_up,
                        COUNT(*) FILTER (WHERE rating = 'down') as thumbs_down
                    FROM public.feedback
                    {agent_filter}
                """, params)
                totals = cur.fetchone()

                # Per-agent breakdown
                cur.execute("""
                    SELECT
                        agent_id,
                        COUNT(*) as total,
                        COUNT(*) FILTER (WHERE rating = 'up') as thumbs_up,
                        COUNT(*) FILTER (WHERE rating = 'down') as thumbs_down
                    FROM public.feedback
                    GROUP BY agent_id
                    ORDER BY agent_id
                """)
                per_agent = cur.fetchall()

                # Recent feedback entries (last 50)
                cur.execute(f"""
                    SELECT id, agent_id, thread_id, message_index, rating, user_id, created_at
                    FROM public.feedback
                    {agent_filter}
                    ORDER BY created_at DESC
                    LIMIT 50
                """, params)
                recent = cur.fetchall()

                # Convert datetime to string for JSON serialization
                for entry in recent:
                    entry["created_at"] = entry["created_at"].isoformat()

        # Enrich recent entries with the actual message content
        # Group by (agent_id, thread_id) to avoid redundant loads
        session_cache = {}  # (agent_id, thread_id) → [messages]
        for entry in recent:
            key = (entry["agent_id"], entry["thread_id"])
            if key not in session_cache:
                session_cache[key] = _load_session_messages(entry["agent_id"], entry["thread_id"])
            messages = session_cache[key]
            idx = entry["message_index"]

            # The frontend includes a greeting message at index 0 that isn't
            # stored in LangGraph, so the stored message_index is offset by 1.
            # Try idx-1 first (greeting offset), then idx as fallback.
            resolved_idx = None
            for candidate in (idx - 1, idx):
                if messages and 0 <= candidate < len(messages) and messages[candidate]["type"] == "ai":
                    resolved_idx = candidate
                    break

            if resolved_idx is not None:
                entry["message_content"] = messages[resolved_idx]["content"]
                # Walk backward to find the preceding human message (user question)
                entry["user_question"] = None
                for j in range(resolved_idx - 1, -1, -1):
                    if messages[j]["type"] == "human":
                        entry["user_question"] = messages[j]["content"]
                        break
            else:
                entry["message_content"] = None
                entry["user_question"] = None

        return {
            "total_feedback": totals["total_feedback"],
            "thumbs_up": totals["thumbs_up"],
            "thumbs_down": totals["thumbs_down"],
            "per_agent": per_agent,
            "recent": recent,
        }

    except Exception as exc:
        logger.error(f"Failed to get feedback stats: {exc}")
        raise HTTPException(status_code=500, detail=f"Database error: {exc}")
