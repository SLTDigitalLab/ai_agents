"""
Chat router - connects the React frontend to the LangGraph agent system with streaming support.
Includes input guardrails (LLM-based intent + sentiment classification) run before the agent.
"""

import json
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

# Hidden metadata block the frontend parses into the LifeStore product-card
# slideshow. The LLM is *asked* to emit this in its answer, but small models
# (gpt-4o-mini) skip it unreliably. We append it deterministically after the
# stream from the last tool result so the slideshow renders every time image-
# ready products were returned. Must match frontend/src/components/ChatInterface.jsx.
PRODUCT_CARDS_START = "[LIFESTORE_PRODUCT_CARDS]"
PRODUCT_CARDS_END = "[/LIFESTORE_PRODUCT_CARDS]"
PRODUCT_CARD_MAX_ITEMS = 24


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


def _message_content_to_text(content, strip: bool = True) -> str:
    """Normalize LangChain message content into plain text."""
    if isinstance(content, str):
        return content.strip() if strip else content

    if isinstance(content, list):
        text_parts = []
        for block in content:
            if isinstance(block, str):
                text_parts.append(block)
            elif isinstance(block, dict) and "text" in block:
                text_parts.append(str(block["text"]))
        merged = _join_text_parts(text_parts)
        return merged.strip() if strip else merged

    if content is None:
        return ""

    return str(content).strip() if strip else str(content)


def _tool_output_to_text(output) -> str:
    """Normalize an ``on_tool_end`` event output into plain text."""
    content = getattr(output, "content", output)
    if isinstance(content, str):
        return content
    return _message_content_to_text(content, strip=False)


def _parse_product_cards_from_text(text: str) -> tuple[list, str]:
    """
    Parse an image-safe ``product_cards`` list out of a single LifeStore tool
    output string.

    The LifeStore tool wrappers return JSON that includes ``product_cards``
    (the image-safe frontend-ready subset) and a ``display`` hint. Returns
    ``(product_cards, display)`` or ``([], "")`` when the payload has none.
    """
    if not text or "product_cards" not in text:
        return [], ""

    data = None
    try:
        data = json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start : end + 1])
            except Exception:
                data = None

    if not isinstance(data, dict):
        return [], ""

    cards = data.get("product_cards")
    if isinstance(cards, list) and cards:
        display = str(data.get("display") or ("carousel" if len(cards) > 1 else "single"))
        return cards[:PRODUCT_CARD_MAX_ITEMS], display

    return [], ""


def _extract_current_turn_product_cards(tool_output_texts: list) -> tuple[list, str]:
    """
    Find the most recent product-card payload among tool outputs produced in
    the CURRENT turn only.

    Scoping to the current run (rather than the full checkpointed thread
    history) prevents a stale product search from re-injecting a slideshow on
    a later, unrelated turn such as add-to-cart or checkout.
    """
    for text in reversed(tool_output_texts):
        cards, display = _parse_product_cards_from_text(text)
        if cards:
            return cards, display
    return [], ""


def _build_product_cards_block(cards: list, display: str) -> str:
    """Render the hidden product-card block the frontend slideshow parses."""
    payload = json.dumps({"display": display, "products": cards}, ensure_ascii=False)
    return f"\n\n{PRODUCT_CARDS_START}{payload}{PRODUCT_CARDS_END}"


@router.post("")
async def chat(request: ChatRequest):
    """Handle an incoming chat message from the frontend with streaming."""
    try:
        builder_fn = get_agent_builder(request.agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    
    # non-streaming path — used by voice agent so it gets a complete answer
    if not request.stream:
        full_text = ""
        try:
            builder_fn = get_agent_builder(request.agent_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

        async def collect() -> str:
            collected = ""
            guardrail = await classify_intent(request.message)
            if guardrail.action == "BLOCK":
                return BLOCK_MESSAGE

            state = {
                "messages": [("user", request.message)],
                "agent_id": request.agent_id,
                "user_id": request.user_id,
                "form_slots": {},
                "next_node": "",
                "sentiment": guardrail.sentiment,
            }

            SUPPRESS_STREAM_NODES = {"multi_delegate", "decompose_query"}

            async with get_async_postgres_checkpointer(request.agent_id) as checkpointer:
                workflow = builder_fn()
                graph = workflow.compile(checkpointer=checkpointer)

                async for event in graph.astream_events(state, {"configurable": {"thread_id": request.thread_id}}, version="v2"):
                    if event["event"] == "on_chat_model_stream":
                        metadata = event.get("metadata") or {}
                        node = metadata.get("langgraph_node")
                        checkpoint_ns = metadata.get("langgraph_checkpoint_ns") or ""
                        suppressed = (
                            node in SUPPRESS_STREAM_NODES
                            or any(n in checkpoint_ns for n in SUPPRESS_STREAM_NODES)
                        )
                        if suppressed:
                            continue
                        content = event["data"]["chunk"].content
                        text = _message_content_to_text(content, strip=False)
                        if text:
                            collected += text

                if not collected:
                    snapshot = await graph.aget_state({"configurable": {"thread_id": request.thread_id}})
                    if snapshot.values:
                        for msg in reversed(snapshot.values.get("messages", [])):
                            if msg.type == "ai":
                                t = _message_content_to_text(msg.content)
                                if t:
                                    collected = t
                                    break

            return collected

        full_text = await collect()
        return {"response": full_text}

    

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            # Thread config enables LangGraph memory/checkpointing per conversation.
            # user_id is also passed through so LifeStore cart tools can keep a
            # customer cart across logout/login without changing the chat API.
            config = {
                "configurable": {
                    "thread_id": request.thread_id,
                    "user_id": request.user_id,
                }
            }

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
                streamed_text = ""
                # Tool outputs produced in THIS turn only, used to append the
                # product-card slideshow deterministically. Scoped to the
                # current run so a stale search does not re-inject on later turns.
                tool_output_texts: list[str] = []

                project_name = f"Ask SLT - {request.agent_id.upper()}"
                with tracing_v2_enabled(project_name=project_name):
                    # We use astream_events (v2) for fine-grained streaming.
                    #
                    # Nodes whose token stream must be SUPPRESSED — these fan
                    # out multiple concurrent LLM calls whose tokens would
                    # otherwise interleave in the HTTP stream and render as
                    # garbled text on the client. The final merged reply from
                    # the downstream synthesis node still streams cleanly.
                    #
                    # Specialists invoked inside multi_delegate run as compiled
                    # subgraphs, so their LLM events bubble up with the subgraph's
                    # internal node name ("agent") rather than the parent node.
                    # We must also match on ``langgraph_checkpoint_ns`` (a
                    # namespace string like "multi_delegate:<hash>|agent:<hash>")
                    # to suppress nested events too.
                    SUPPRESS_STREAM_NODES = {"multi_delegate", "decompose_query"}
                    logged_metadata_sample = False

                    # ── DeepSeek-R1 <think> stripper ─────────────
                    # The internal SLM (deepseek-r1) prefixes its output
                    # with <think>...</think> reasoning tokens. We hide
                    # those from the user but let the final answer stream.
                    strip_think = request.agent_id == "askhrslm"
                    think_buffer = ""
                    in_think_block = False
                    think_done = False

                    async for event in graph.astream_events(state, config, version="v2"):
                        # ── Extract tokens from stream events ────────
                        kind = event["event"]

                        if kind == "on_tool_end":
                            # Capture this turn's tool outputs so we can append
                            # the product-card slideshow after the answer streams.
                            tool_text = _tool_output_to_text((event.get("data") or {}).get("output"))
                            if tool_text:
                                tool_output_texts.append(tool_text)
                            continue

                        if kind == "on_chat_model_stream":
                            metadata = event.get("metadata") or {}

                            if not logged_metadata_sample:
                                logger.info(
                                    "Stream metadata sample | node=%r | checkpoint_ns=%r | tags=%r",
                                    metadata.get("langgraph_node"),
                                    metadata.get("langgraph_checkpoint_ns"),
                                    event.get("tags"),
                                )
                                logged_metadata_sample = True

                            node = metadata.get("langgraph_node")
                            checkpoint_ns = metadata.get("langgraph_checkpoint_ns") or ""
                            suppressed = (
                                node in SUPPRESS_STREAM_NODES
                                or any(
                                    suppressed_node in checkpoint_ns
                                    for suppressed_node in SUPPRESS_STREAM_NODES
                                )
                            )
                            if suppressed:
                                continue

                            content = event["data"]["chunk"].content
                            text = _message_content_to_text(content, strip=False)

                            if text and strip_think and not think_done:
                                think_buffer += text
                                if not in_think_block and "<think>" in think_buffer:
                                    in_think_block = True
                                    think_buffer = think_buffer.split("<think>", 1)[1]
                                    text = ""
                                if in_think_block:
                                    if "</think>" in think_buffer:
                                        # Drop everything up to and including </think>;
                                        # whatever follows is the real answer.
                                        text = think_buffer.split("</think>", 1)[1].lstrip()
                                        in_think_block = False
                                        think_done = True
                                        think_buffer = ""
                                    else:
                                        text = ""  # still inside think block
                                elif "<think>" not in think_buffer:
                                    # No think tag at all — flush buffer as normal output.
                                    text = think_buffer
                                    think_buffer = ""
                                    think_done = True

                            if text:
                                streamed_any_text = True
                                streamed_text += text
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
                                    streamed_text += text
                                    yield text
                                    break

                # Deterministic product-card injection.
                # The LLM is asked to append the hidden [LIFESTORE_PRODUCT_CARDS]
                # block, but small models skip it unreliably. If it didn't emit
                # one, append it ourselves from the last tool result's image-safe
                # product_cards so the frontend slideshow renders every time.
                if PRODUCT_CARDS_START not in streamed_text:
                    try:
                        cards, display = _extract_current_turn_product_cards(tool_output_texts)
                        if cards:
                            logger.info(
                                "Injected %d LifeStore product card(s) | agent=%s | thread=%s",
                                len(cards),
                                request.agent_id,
                                request.thread_id,
                            )
                            yield _build_product_cards_block(cards, display)
                    except Exception as inject_exc:
                        logger.warning("Product-card injection skipped: %s", inject_exc)

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
