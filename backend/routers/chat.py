"""
Chat router - connects the React frontend to the LangGraph agent system with streaming support.
Includes input guardrails (LLM-based intent classification) running in parallel with the agent.
"""

import asyncio
import json
import logging
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from core.checkpointer import get_postgres_checkpointer, get_async_postgres_checkpointer
from domain.registry import get_agent_builder
from domain.guardrails import classify_intent
from schemas.chat import ChatRequest
from langchain_core.tracers.context import tracing_v2_enabled

router = APIRouter(prefix="/api/v1/chat", tags=["Chat"])
logger = logging.getLogger(__name__)

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
            
            # ── Start guardrail classifier IN PARALLEL with agent ────────
            classify_task = asyncio.create_task(classify_intent(request.message))

            # Initial state (sentiment will be updated once classifier finishes)
            state = {
                "messages": [("user", request.message)],
                "agent_id": request.agent_id,
                "user_id": request.user_id,
                "form_slots": {},
                "next_node": "",
                "sentiment": "neutral",
            }

            # Use the ASYNC checkpointer for streaming – required by astream_events
            async with get_async_postgres_checkpointer(request.agent_id) as checkpointer:
                workflow = builder_fn()
                graph = workflow.compile(checkpointer=checkpointer)

                project_name = f"Ask SLT - {request.agent_id.upper()}"
                with tracing_v2_enabled(project_name=project_name):
                    buffer = []             # holds tokens until classifier finishes
                    classifier_done = False

                    # We use astream_events (v2) for fine-grained streaming
                    async for event in graph.astream_events(state, config, version="v2"):
                        # ── Check classifier on each event ───────────
                        if not classifier_done and classify_task.done():
                            guardrail = classify_task.result()
                            classifier_done = True
                            if guardrail.action == "BLOCK":
                                yield "I'm sorry, but I'm unable to help with that request."
                                return
                            logger.info(
                                f"Guardrail PASS | sentiment={guardrail.sentiment} | "
                                f"reason={guardrail.reason}"
                            )
                            # Flush buffered tokens now that we have PASS
                            for tok in buffer:
                                yield tok
                            buffer.clear()

                        # ── Extract tokens from stream events ────────
                        kind = event["event"]
                        if kind == "on_chat_model_stream":
                            content = event["data"]["chunk"].content
                            if content:
                                # If it's a list of blocks, extract text
                                if isinstance(content, list):
                                    for block in content:
                                        if isinstance(block, dict) and "text" in block:
                                            token = block["text"]
                                        elif isinstance(block, str):
                                            token = block
                                        else:
                                            continue
                                        if classifier_done:
                                            yield token
                                        else:
                                            buffer.append(token)
                                else:
                                    token = str(content)
                                    if classifier_done:
                                        yield token
                                    else:
                                        buffer.append(token)

                    # ── Final check if classifier hasn't finished yet ──
                    if not classifier_done:
                        guardrail = await classify_task
                        if guardrail.action == "BLOCK":
                            yield "I'm sorry, but I'm unable to help with that request."
                            return
                        logger.info(
                            f"Guardrail PASS (late) | sentiment={guardrail.sentiment}"
                        )
                        for tok in buffer:
                            yield tok

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
                            text_parts.append(block["text"])
                    content = "".join(text_parts).strip()
                elif not isinstance(content, str):
                    content = str(content).strip()
                
                if content:
                    messages.append({
                        "type": msg.type,
                        "content": content
                    })
            
            return {"messages": messages}

    except Exception as exc:
        logger.error(f"Error fetching history: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))
