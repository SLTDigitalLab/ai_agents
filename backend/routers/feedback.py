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
from domain.registry import AGENT_BUILDERS
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
