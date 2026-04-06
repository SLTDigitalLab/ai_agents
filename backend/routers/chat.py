"""
Chat router - connects the React frontend to the LangGraph agent system.

POST /api/v1/chat  →  Compiles the agent graph on-the-fly with a per-request
database connection to ensure clean resource cleanup.

Redis may short-circuit the LLM while still running the graph so Postgres
checkpoints stay consistent with the conversation.
"""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from langchain_core.tracers.context import tracing_v2_enabled

from core.cache_keys import (
    build_cache_key,
    should_skip_cache_for_message,
    should_use_cache_for_agent,
)
from core.checkpointer import get_postgres_checkpointer
from core.redis_client import get_redis_client
from domain.registry import get_agent_builder
from schemas.chat import ChatRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/chat", tags=["Chat"])

CACHE_TTL_SECONDS = 86400
MIN_CACHEABLE_RESPONSE_LEN = 12


def _normalize_final_message(final_message: Any) -> str:
    """Match previous behavior: Gemini may return list/dict blocks."""
    if isinstance(final_message, list):
        text_parts = []
        for block in final_message:
            if isinstance(block, str):
                text_parts.append(block)
            elif isinstance(block, dict) and "text" in block:
                text_parts.append(block["text"])
        return " ".join(text_parts)
    if not isinstance(final_message, str):
        return str(final_message)
    return final_message


def _build_invoke_config(thread_id: str, cached_response: str | None = None) -> dict:
    cfg: dict[str, Any] = {"thread_id": thread_id}
    if cached_response is not None:
        cfg["cached_response"] = cached_response
    return {"configurable": cfg}


def _conversation_has_prior_turns(snapshot_values: dict | None) -> bool:
    msgs = (snapshot_values or {}).get("messages") or []
    return len(msgs) > 0


@router.post("")  # Mounts at /api/v1/chat (no trailing slash)
async def chat(request: ChatRequest):
    """Handle an incoming chat message from the frontend."""
    try:
        print(f"DEBUG: Received agent_id: {request.agent_id}")
        builder_fn = get_agent_builder(request.agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    workflow = builder_fn()
    config = _build_invoke_config(request.thread_id)

    redis_client = get_redis_client()
    cache_key: str | None = None
    cache_allowed = (
        request.use_cache
        and redis_client is not None
        and should_use_cache_for_agent(request.agent_id)
        and not should_skip_cache_for_message(request.message)
    )

    try:
        with get_postgres_checkpointer(request.agent_id) as checkpointer:
            graph = workflow.compile(checkpointer=checkpointer)

            snapshot = graph.get_state(config)
            prior = _conversation_has_prior_turns(snapshot.values)
            if prior:
                cache_allowed = False

            cached_text: str | None = None
            if cache_allowed:
                cache_key = build_cache_key(request.agent_id, request.message)
                try:
                    cached_text = redis_client.get(cache_key)
                except Exception as exc:
                    logger.warning("Redis GET failed; continuing without cache: %s", exc)
                    cached_text = None

            invoke_config = (
                _build_invoke_config(request.thread_id, cached_text)
                if cached_text
                else config
            )

            state = {
                "messages": [("user", request.message)],
                "agent_id": request.agent_id,
                "user_id": request.user_id,
                "form_slots": {},
                "next_node": "",
            }

            project_name = f"Ask SLT - {request.agent_id.upper()}"
            with tracing_v2_enabled(project_name=project_name):
                result = graph.invoke(state, config=invoke_config)

            final_message = result["messages"][-1].content
            final_text = _normalize_final_message(final_message)

            response_cached = bool(cached_text)
            if (
                cache_allowed
                and cache_key
                and redis_client is not None
                and not response_cached
                and len(final_text.strip()) >= MIN_CACHEABLE_RESPONSE_LEN
            ):
                try:
                    redis_client.setex(cache_key, CACHE_TTL_SECONDS, final_text)
                except Exception as exc:
                    logger.warning("Redis SET failed: %s", exc)

            return {
                "response": final_text,
                "cached": response_cached,
            }

    except Exception as exc:
        import traceback

        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Agent execution failed: {exc}",
        )


@router.get("/{agent_id}/{thread_id}")
async def get_history(agent_id: str, thread_id: str):
    """Retrieve the chat history for a specific session."""
    try:
        builder_fn = get_agent_builder(agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    workflow = builder_fn()
    config = _build_invoke_config(thread_id)

    try:
        with get_postgres_checkpointer(agent_id) as checkpointer:
            graph = workflow.compile(checkpointer=checkpointer)

            # Get the current state snapshot from the database
            snapshot = graph.get_state(config)

            if not snapshot.values:
                return {"messages": []}

            # return a simplified list of messages
            # snapshot.values['messages'] is a list of LangChain objects
            messages = []
            for msg in snapshot.values.get("messages", []):
                # Only expose Human and AI messages to the frontend
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

                # Skip empty messages (e.g., AI messages that only performed a tool call but had no text)
                if content:
                    messages.append(
                        {
                            "type": msg.type,
                            "content": content,
                        }
                    )

            return {"messages": messages}

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
