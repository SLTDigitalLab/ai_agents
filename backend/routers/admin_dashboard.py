"""
Admin Dashboard router – provides read-only endpoints for the admin panel
to browse chat sessions stored by LangGraph's PostgresSaver.

GET /api/v1/admin/dashboard/stats              → dashboard metrics
GET /api/v1/admin/dashboard/sessions           → paginated master list
GET /api/v1/admin/dashboard/sessions/{agent}/{session_id}  → full conversation
"""

import re
from fastapi import APIRouter, HTTPException, Query

import psycopg
from psycopg.rows import dict_row

from core.config import settings
from core.checkpointer import get_postgres_checkpointer
from domain.registry import AGENT_BUILDERS, get_agent_builder

router = APIRouter(prefix="/api/v1/admin/dashboard", tags=["Admin Dashboard"])

# ── Helpers ──────────────────────────────────────────────────────────────

VALID_AGENTS = set(AGENT_BUILDERS.keys())


def _sanitize_schema_name(agent_id: str) -> str:
    """Mirror the same logic used in core/checkpointer.py."""
    clean = re.sub(r"[^a-z0-9_]", "_", agent_id.lower())
    return f"agent_{clean}"


def _validate_agent(agent: str) -> str:
    """Validate agent against the registry to prevent SQL injection."""
    if agent not in VALID_AGENTS:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown agent '{agent}'. Valid: {sorted(VALID_AGENTS)}",
        )
    return agent


# ── Endpoint A: Lightweight Master List ──────────────────────────────────

@router.get("/sessions")
async def list_sessions(
    agent: str = Query(..., description="Agent ID, e.g. 'hr', 'finance'"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    search: str = Query("", description="Search term to filter sessions by message content"),
):
    """Return a paginated list of chat sessions for the given agent.

    Each item contains: session_id, message_count, preview_text.
    When `search` is provided, all sessions are loaded and filtered by
    whether any message content matches the search term (case-insensitive).
    Pagination is applied *after* filtering.
    """
    _validate_agent(agent)
    schema = _sanitize_schema_name(agent)
    search_term = search.strip().lower()

    # 1. Get distinct thread IDs from the checkpoints table
    #    When searching, fetch ALL threads (no DB-level pagination)
    #    so we can filter after deserialization.
    try:
        with psycopg.connect(settings.POSTGRES_URL, autocommit=True) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                if search_term:
                    # Fetch all threads sorted by recency (search filtering happens after deserialization)
                    cur.execute(f"""
                        SELECT * FROM (
                            SELECT DISTINCT ON (thread_id)
                                thread_id,
                                checkpoint_id
                            FROM {schema}.checkpoints
                            WHERE checkpoint_ns = ''
                            ORDER BY thread_id, checkpoint_id DESC
                        ) sub
                        ORDER BY checkpoint_id DESC
                    """)
                else:
                    # No search → use DB-level pagination, most recent first
                    cur.execute(f"""
                        SELECT * FROM (
                            SELECT DISTINCT ON (thread_id)
                                thread_id,
                                checkpoint_id
                            FROM {schema}.checkpoints
                            WHERE checkpoint_ns = ''
                            ORDER BY thread_id, checkpoint_id DESC
                        ) sub
                        ORDER BY checkpoint_id DESC
                        LIMIT %s OFFSET %s
                    """, (limit, skip))
                rows = cur.fetchall()

                # Total count (before search filtering)
                cur.execute(f"""
                    SELECT COUNT(DISTINCT thread_id) as total
                    FROM {schema}.checkpoints
                    WHERE checkpoint_ns = ''
                """)
                db_total = cur.fetchone()["total"]

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Database error: {exc}",
        )

    if not rows:
        return {"sessions": [], "total": 0, "skip": skip, "limit": limit}

    # 2. For each thread, deserialize messages and optionally filter by search
    all_sessions = []
    builder_fn = get_agent_builder(agent)
    workflow = builder_fn()

    for row in rows:
        thread_id = row["thread_id"]
        config = {"configurable": {"thread_id": thread_id}}

        try:
            with get_postgres_checkpointer(agent) as checkpointer:
                graph = workflow.compile(checkpointer=checkpointer)
                snapshot = graph.get_state(config)

                if not snapshot.values:
                    continue

                raw_messages = snapshot.values.get("messages", [])
                messages = [m for m in raw_messages if m.type in ("human", "ai")]
                message_count = len(messages)

                # Extract all message text for search matching
                all_text_parts = []
                preview_text = ""
                for msg in messages:
                    content = msg.content
                    if isinstance(content, list):
                        parts = []
                        for block in content:
                            if isinstance(block, str):
                                parts.append(block)
                            elif isinstance(block, dict) and "text" in block:
                                parts.append(block["text"])
                        content = " ".join(parts)
                    else:
                        content = str(content)

                    all_text_parts.append(content)

                    # First human message = preview
                    if not preview_text and msg.type == "human":
                        preview_text = content[:120]

                # If searching, check if ANY message matches
                if search_term:
                    combined_text = " ".join(all_text_parts).lower()
                    if search_term not in combined_text:
                        continue  # Skip this session — doesn't match

                all_sessions.append({
                    "session_id": thread_id,
                    "message_count": message_count,
                    "preview_text": preview_text or "(no messages)",
                })

        except Exception as exc:
            print(f"Warning: Failed to load session {thread_id}: {exc}")
            if not search_term:
                all_sessions.append({
                    "session_id": thread_id,
                    "message_count": 0,
                    "preview_text": "(failed to load)",
                })

    # 3. Apply pagination on the (possibly filtered) results
    if search_term:
        total = len(all_sessions)
        sessions = all_sessions[skip : skip + limit]
    else:
        total = db_total
        sessions = all_sessions

    return {
        "sessions": sessions,
        "total": total,
        "skip": skip,
        "limit": limit,
    }


# ── Endpoint B: Heavy Detail View ───────────────────────────────────────

@router.get("/sessions/{agent}/{session_id}")
async def get_session_detail(agent: str, session_id: str):
    """Return the full conversation history for a specific session.

    Uses the same LangGraph deserialization as the chat history endpoint.
    """
    _validate_agent(agent)

    try:
        builder_fn = get_agent_builder(agent)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    workflow = builder_fn()
    config = {"configurable": {"thread_id": session_id}}

    try:
        with get_postgres_checkpointer(agent) as checkpointer:
            graph = workflow.compile(checkpointer=checkpointer)
            snapshot = graph.get_state(config)

            if not snapshot.values:
                return {"session_id": session_id, "agent": agent, "messages": []}

            # Extract and clean messages (same pattern as chat.py get_history)
            messages = []
            for msg in snapshot.values.get("messages", []):
                if msg.type not in ("human", "ai"):
                    continue

                content = msg.content
                if isinstance(content, list):
                    text_parts = []
                    for block in content:
                        if isinstance(block, str):
                            text_parts.append(block)
                        elif isinstance(block, dict) and "text" in block:
                            text_parts.append(block["text"])
                    content = " ".join(text_parts).strip()
                elif not isinstance(content, str):
                    content = str(content).strip()

                if content:
                    messages.append({
                        "type": msg.type,
                        "content": content,
                    })

            return {
                "session_id": session_id,
                "agent": agent,
                "messages": messages,
            }

    except Exception as exc:
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load session: {exc}",
        )


# ── Endpoint C: Dashboard Stats ─────────────────────────────────────────

@router.get("/stats")
async def get_dashboard_stats():
    """Return aggregate metrics for the admin dashboard.

    Returns total session count across all agents plus per-agent breakdown.
    """
    agent_stats = []
    total_sessions = 0

    try:
        with psycopg.connect(settings.POSTGRES_URL, autocommit=True) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                for agent_id in sorted(VALID_AGENTS):
                    schema = _sanitize_schema_name(agent_id)
                    try:
                        cur.execute(f"""
                            SELECT COUNT(DISTINCT thread_id) as session_count
                            FROM {schema}.checkpoints
                            WHERE checkpoint_ns = ''
                        """)
                        count = cur.fetchone()["session_count"]
                    except Exception:
                        count = 0

                    agent_stats.append({
                        "agent_id": agent_id,
                        "session_count": count,
                    })
                    total_sessions += count

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Database error: {exc}",
        )

    return {
        "total_sessions": total_sessions,
        "agents": agent_stats,
        "agent_count": len(VALID_AGENTS),
    }

