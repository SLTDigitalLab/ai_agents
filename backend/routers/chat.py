"""
Chat router - connects the React frontend to the LangGraph agent system with streaming support.
Includes input guardrails (LLM-based intent + sentiment classification) run before the agent.
"""

import logging
import re
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage, AIMessage

from core.checkpointer import get_postgres_checkpointer, get_async_postgres_checkpointer
from domain.registry import get_agent_builder
from domain.guardrails import classify_intent
from schemas.chat import ChatRequest
from langchain_core.tracers.context import tracing_v2_enabled

router = APIRouter(prefix="/api/v1/chat", tags=["Chat"])
logger = logging.getLogger(__name__)

BLOCK_MESSAGE = "I'm sorry, but I'm unable to help with that request."


def _join_text_parts(parts: list[str]) -> str:
    """Join fragmented text blocks without crushing words together."""
    merged = ""

    for raw in parts:
        text = str(raw or "")
        if not text:
            continue

        if not merged:
            merged = text
            continue

        prev = merged[-1]
        nxt = text[0]

        should_insert_space = (
            not prev.isspace()
            and not nxt.isspace()
            and (
                (prev.isalnum() and nxt.isalnum())
                or (prev in ".!?,:;)" and (nxt.isalnum() or nxt == "("))
            )
        )

        if should_insert_space:
            merged += " "

        merged += text

    return re.sub(r"[ \t]+", " ", merged).strip()


def _message_content_to_text(content) -> str:
    """Normalize LangChain message content into plain text."""
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        text_parts = []
        for block in content:
            if isinstance(block, str):
                text_parts.append(block)
            elif isinstance(block, dict) and "text" in block:
                text_parts.append(str(block["text"]))
        return _join_text_parts(text_parts)

    if content is None:
        return ""

    return str(content).strip()


@router.post("")
async def chat(request: ChatRequest):
    """Handle an incoming chat message from the frontend with streaming."""
    try:
        builder_fn = get_agent_builder(request.agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            # Thread config enables LangGraph memory/checkpointing per conversation
            config = {"configurable": {"thread_id": request.thread_id}}

            # ── Run guardrail classifier FIRST ──────────────────────────
            # gpt-4.1-nano is ~100-200ms, so this adds minimal latency
            # and lets us pass real sentiment into the agent state.
            guardrail = await classify_intent(request.message)

            if guardrail.action == "BLOCK":
                logger.info(f"Guardrail BLOCK | reason={guardrail.reason}")
                # Save the blocked exchange to chat history
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
                        await graph.aupdate_state(config, blocked_state)
                except Exception as e:
                    logger.warning(f"Failed to save blocked exchange: {e}")

                yield BLOCK_MESSAGE
                return

            logger.info(
                f"Guardrail PASS | sentiment={guardrail.sentiment} | "
                f"reason={guardrail.reason}"
            )

            # Build initial state with real sentiment from classifier
            state = {
                "messages": [("user", request.message)],
                "agent_id": request.agent_id,
                "user_id": request.user_id,
                "form_slots": {},
                "next_node": "",
                "sentiment": guardrail.sentiment,
            }

            # Use the ASYNC checkpointer for streaming – required by astream_events
            async with get_async_postgres_checkpointer(request.agent_id) as checkpointer:
                workflow = builder_fn()
                graph = workflow.compile(checkpointer=checkpointer)

                streamed_any_text = False

                project_name = f"Ask SLT - {request.agent_id.upper()}"
                with tracing_v2_enabled(project_name=project_name):
                    # We use astream_events (v2) for fine-grained streaming
                    async for event in graph.astream_events(state, config, version="v2"):
                        # ── Extract tokens from stream events ────────
                        kind = event["event"]

                        if kind == "on_chat_model_stream":
                            content = event["data"]["chunk"].content
                            text = _message_content_to_text(content)

                            if text:
                                streamed_any_text = True
                                yield text

                # Fallback: if the graph responded without streaming tokens,
                # fetch the latest AI message from final graph state.
                if not streamed_any_text:
                    snapshot = await graph.aget_state(config)

                    if snapshot.values:
                        messages = snapshot.values.get("messages", [])

                        for msg in reversed(messages):
                            if msg.type == "ai":
                                text = _message_content_to_text(msg.content)
                                if text:
                                    logger.info(
                                        "Non-streaming fallback response used | agent=%s | thread=%s",
                                        request.agent_id,
                                        request.thread_id,
                                    )
                                    yield text
                                    break

        except Exception as exc:
            import traceback
            error_details = traceback.format_exc()
            logger.error(f"Streaming error: {exc}\n{error_details}")
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
    config = {"configurable": {"thread_id": thread_id}}

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
                            text_parts.append(str(block["text"]))
                    content = _join_text_parts(text_parts)
                elif not isinstance(content, str):
                    content = str(content).strip()
                else:
                    content = content.strip()

                if content:
                    messages.append({
                        "type": msg.type,
                        "content": content
                    })

            return {"messages": messages}

    except Exception as exc:
        logger.error(f"Error fetching history: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))