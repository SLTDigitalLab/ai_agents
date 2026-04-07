"""
Chat router - connects the React frontend to the LangGraph agent system with streaming support.
Includes input guardrails (LLM-based intent + sentiment classification) run before the agent.

POST /api/v1/chat compiles the agent graph per request with an async Postgres checkpointer.
Redis may short-circuit the LLM while still running the graph so checkpoints stay consistent.
"""

import logging
from typing import Any, AsyncGenerator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tracers.context import tracing_v2_enabled

from core.cache_keys import (
    build_cache_key,
    normalize_question,
    should_skip_cache_for_message,
    should_use_cache_for_agent,
)
from core.checkpointer import get_async_postgres_checkpointer, get_postgres_checkpointer
from core.redis_client import get_redis_client
from core.fuzzy_match import FUZZY_QUESTIONS_LIST_KEY, append_question_to_redis_list, fuzzy_lookup_from_redis
from core.semantic_cache import semantic_lookup as qdrant_semantic_lookup
from core.semantic_cache import semantic_store as qdrant_semantic_store
from domain.guardrails import classify_intent
from domain.registry import get_agent_builder
from schemas.chat import ChatRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/chat", tags=["Chat"])

CACHE_TTL_SECONDS = 86400
MIN_CACHEABLE_RESPONSE_LEN = 12

FUZZY_THRESHOLD = 90.0
FUZZY_SCAN_LIMIT = 500
SEMANTIC_SIMILARITY_THRESHOLD = 0.85

BLOCK_MESSAGE = "I'm sorry, but I'm unable to help with that request."


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


def _last_ai_text_from_messages(msgs: list) -> str | None:
    for msg in reversed(msgs):
        if getattr(msg, "type", None) == "ai":
            return _normalize_final_message(msg.content)
    return None


def exact_lookup(
    redis_client,
    *,
    agent_id: str,
    message: str,
) -> tuple[str | None, str | None]:
    """
    Layer 1: Exact match cache.

    IMPORTANT: This preserves the existing behavior:
    - normalize (trim + lowercase) inside build_cache_key()
    - sha256 hash key in Redis
    - GET returns cached response string
    """
    if redis_client is None:
        return None, None

    cache_key = build_cache_key(agent_id, message)
    try:
        cached_text = redis_client.get(cache_key)
        return cached_text, cache_key
    except Exception as exc:
        logger.warning("Redis GET failed; continuing without cache: %s", exc)
        return None, cache_key


def fuzzy_lookup(
    redis_client,
    *,
    agent_id: str,
    message: str,
) -> str | None:
    """
    Layer 2: Fuzzy match cache (RapidFuzz).

    - Compares normalized incoming question with previously cached questions stored
      in Redis list `cache_questions:list`
    - Threshold: 90%
    - On match: re-derive exact Redis key for matched question and GET the answer
    """
    if redis_client is None:
        return None

    normalized = normalize_question(message)
    match = fuzzy_lookup_from_redis(
        redis_client,
        agent_id=agent_id,
        normalized_question=normalized,
        list_key=FUZZY_QUESTIONS_LIST_KEY,
        scan_limit=FUZZY_SCAN_LIMIT,
        threshold=FUZZY_THRESHOLD,
    )
    if not match:
        return None

    # The exact cache already stores answers keyed by hash(normalized_question),
    # so we can retrieve the answer without duplicating storage.
    try:
        key = build_cache_key(agent_id, match.question)
        ans = redis_client.get(key)
        if isinstance(ans, str) and ans.strip():
            return ans
        return None
    except Exception as exc:
        logger.warning("Redis GET for fuzzy match failed; continuing: %s", exc)
        return None


def semantic_lookup(
    *,
    agent_id: str,
    message: str,
) -> str | None:
    """
    Layer 3: Semantic cache (Qdrant + all-MiniLM-L6-v2 embeddings).

    - Generates embedding for the incoming question
    - Searches Qdrant (top 1) filtered by agent_id
    - Returns cached answer if similarity > 0.85
    """
    try:
        return qdrant_semantic_lookup(
            agent_id=agent_id,
            question=message,
            similarity_threshold=SEMANTIC_SIMILARITY_THRESHOLD,
        )
    except Exception as exc:
        # Defensive: semantic_cache already fail-opens, but keep router robust.
        logger.warning("Semantic cache lookup failed; continuing: %s", exc)
        return None


@router.post("")
async def chat(request: ChatRequest):
    """Handle an incoming chat message from the frontend with streaming."""
    try:
        builder_fn = get_agent_builder(request.agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            base_config = _build_invoke_config(request.thread_id)

            guardrail = await classify_intent(request.message)

            if guardrail.action == "BLOCK":
                logger.info("Guardrail BLOCK | reason=%s", guardrail.reason)
                try:
                    async with get_async_postgres_checkpointer(request.agent_id) as checkpointer:
                        workflow = builder_fn()
                        graph = workflow.compile(checkpointer=checkpointer)
                        blocked_state = {
                            "messages": [
                                HumanMessage(content=request.message),
                                AIMessage(content=BLOCK_MESSAGE),
                            ],
                            "agent_id": request.agent_id,
                            "user_id": request.user_id,
                            "form_slots": {},
                            "next_node": "",
                            "sentiment": guardrail.sentiment,
                        }
                        await graph.aupdate_state(base_config, blocked_state)
                except Exception as e:
                    logger.warning("Failed to save blocked exchange: %s", e)
                yield BLOCK_MESSAGE
                return

            logger.info(
                "Guardrail PASS | sentiment=%s | reason=%s",
                guardrail.sentiment,
                guardrail.reason,
            )

            state = {
                "messages": [("user", request.message)],
                "agent_id": request.agent_id,
                "user_id": request.user_id,
                "form_slots": {},
                "next_node": "",
                "sentiment": guardrail.sentiment,
            }

            redis_client = get_redis_client()
            exact_cache_key: str | None = None

            async with get_async_postgres_checkpointer(request.agent_id) as checkpointer:
                workflow = builder_fn()
                graph = workflow.compile(checkpointer=checkpointer)

                snapshot = await graph.aget_state(base_config)
                cache_allowed = (
                    request.use_cache
                    and should_use_cache_for_agent(request.agent_id)
                    and not should_skip_cache_for_message(request.message)
                    and not _conversation_has_prior_turns(snapshot.values)
                )

                cached_text: str | None = None
                if cache_allowed:
                    # Layer 1: Exact match (existing behavior).
                    cached_text, exact_cache_key = exact_lookup(
                        redis_client,
                        agent_id=request.agent_id,
                        message=request.message,
                    )

                    # Layer 2: Fuzzy (RapidFuzz over past cached questions).
                    if not cached_text:
                        cached_text = fuzzy_lookup(
                            redis_client,
                            agent_id=request.agent_id,
                            message=request.message,
                        )

                    # Layer 3: Semantic (Qdrant + MiniLM embeddings).
                    if not cached_text:
                        cached_text = semantic_lookup(
                            agent_id=request.agent_id,
                            message=request.message,
                        )

                invoke_config = (
                    _build_invoke_config(request.thread_id, cached_text)
                    if cached_text
                    else base_config
                )

                project_name = f"Ask SLT - {request.agent_id.upper()}"
                with tracing_v2_enabled(project_name=project_name):
                    if cached_text:
                        result = await graph.ainvoke(state, config=invoke_config)
                        final_message = result["messages"][-1].content
                        yield _normalize_final_message(final_message)
                    else:
                        async for event in graph.astream_events(
                            state, invoke_config, version="v2"
                        ):
                            kind = event["event"]
                            if kind == "on_chat_model_stream":
                                content = event["data"]["chunk"].content
                                if content:
                                    if isinstance(content, list):
                                        for block in content:
                                            if isinstance(block, dict) and "text" in block:
                                                yield block["text"]
                                            elif isinstance(block, str):
                                                yield block
                                    else:
                                        yield str(content)

                        if cache_allowed:
                            snap = await graph.aget_state(base_config)
                            vals = snap.values or {}
                            final_text = _last_ai_text_from_messages(
                                vals.get("messages") or []
                            )
                            if (
                                final_text
                                and len(final_text.strip())
                                >= MIN_CACHEABLE_RESPONSE_LEN
                            ):
                                normalized_q = normalize_question(request.message)

                                # Storage rule 1: Exact cache (Redis SETEX).
                                # IMPORTANT: Keep existing exact-key behavior unchanged.
                                if redis_client is not None:
                                    try:
                                        key = exact_cache_key or build_cache_key(
                                            request.agent_id, request.message
                                        )
                                        redis_client.setex(
                                            key, CACHE_TTL_SECONDS, final_text
                                        )
                                    except Exception as exc:
                                        logger.warning("Redis SET failed: %s", exc)

                                    # Storage rule 2: Append to fuzzy list.
                                    append_question_to_redis_list(
                                        redis_client,
                                        agent_id=request.agent_id,
                                        normalized_question=normalized_q,
                                    )

                                # Storage rule 3: Semantic cache (Qdrant upsert).
                                try:
                                    qdrant_semantic_store(
                                        agent_id=request.agent_id,
                                        normalized_question=normalized_q,
                                        answer=final_text,
                                    )
                                except Exception as exc:
                                    logger.warning(
                                        "Semantic cache store failed; continuing: %s", exc
                                    )

        except Exception as exc:
            import traceback

            error_details = traceback.format_exc()
            logger.error("Streaming error: %s\n%s", exc, error_details)
            yield f"\n\n[ERROR]: {str(exc)}"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


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
            snapshot = graph.get_state(config)

            if not snapshot.values:
                return {"messages": []}

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
                    content = "".join(text_parts).strip()
                elif not isinstance(content, str):
                    content = str(content).strip()

                if content:
                    messages.append(
                        {
                            "type": msg.type,
                            "content": content,
                        }
                    )

            return {"messages": messages}

    except Exception as exc:
        logger.error("Error fetching history: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
