"""
Chat router - connects the React frontend to the LangGraph agent system with streaming support.
Includes input guardrails (LLM-based intent + sentiment classification) run before the agent.
"""

import json
import logging
import re
from typing import AsyncGenerator
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage, AIMessage

from core.config import settings
from domain.registry import (
    get_agent_builder,
    get_compiled_async_graph,
    get_compiled_sync_graph,
)
from domain.guardrails import classify_intent
from domain.tools.rag_tools import clear_thread_evidence, consume_thread_evidence
from schemas.chat import ChatRequest
from services.sessions import record_session

# --- 1. Import Langfuse CallbackHandler ---
from langfuse.langchain import CallbackHandler

router = APIRouter(prefix="/api/v1/chat", tags=["Chat"])
logger = logging.getLogger(__name__)

BLOCK_MESSAGE = "I'm sorry, but I'm unable to help with that request."

# Shown when the underlying LLM provider is out of quota / rate-limited
# (Gemini ResourceExhausted, OpenAI insufficient_quota / 429, etc.) so the
# user never sees a raw provider error.
BUSY_MESSAGE = (
    "I'm a little busy right now and can't respond at the moment. "
    "Please try again in a few minutes."
)

# Generic fallback for any other unexpected failure.
GENERIC_ERROR_MESSAGE = (
    "Sorry, something went wrong on my end. Please try again."
)

# Substrings that indicate the LLM provider rejected the call due to
# quota/rate limits rather than a real application bug.
_QUOTA_ERROR_MARKERS = (
    "quota",
    "rate limit",
    "ratelimit",
    "429",
    "resourceexhausted",
    "resource_exhausted",
    "insufficient_quota",
    "too many requests",
    "exceeded your current quota",
)


def _is_quota_error(exc: Exception) -> bool:
    """True if the exception looks like a provider quota / rate-limit error."""
    text = f"{type(exc).__name__} {exc}".lower()
    return any(marker in text for marker in _QUOTA_ERROR_MARKERS)

# Product agents skip query PII masking: model/SKU numbers in product
# searches would otherwise be mangled (e.g. masked as [PHONE]/[CARD_NUMBER])
# and degrade retrieval. These agents are owned/managed separately.
PII_MASK_EXEMPT_AGENTS = {"lifestore", "enterprise"}

# ── Guardrail helpers: PII masking + URL validation ─────────────────────
PII_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Email addresses
    (
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        "[EMAIL]",
    ),
    # Sri Lankan mobile numbers: +947XXXXXXXX / 947XXXXXXXX / 07XXXXXXXX
    (
        re.compile(r"\b(?:\+?94|0)?7\d{8}\b"),
        "[PHONE]",
    ),
    # Sri Lankan NIC: old 123456789V / new 12 digits
    (
        re.compile(r"\b(?:\d{9}[VXvx]|\d{12})\b"),
        "[NIC]",
    ),
    # Credit/debit-card-like long numbers
    (
        re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
        "[CARD_NUMBER]",
    ),
    # Common API keys / JWT / bearer-token-like secrets
    (
        re.compile(
            r"(?i)\b(?:bearer\s+)?("
            r"eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+"
            r"|sk-[a-zA-Z0-9_-]{20,}"
            r"|AIza[0-9A-Za-z_-]{20,}"
            r")\b"
        ),
        "[SECRET]",
    ),
]


def mask_pii(text: str) -> str:
    """Mask common PII/secrets before sending text deeper into the agent."""
    masked = text or ""

    for pattern, replacement in PII_PATTERNS:
        masked = pattern.sub(replacement, masked)

    return masked


def _is_safe_url(value: str) -> bool:
    """Allow only safe evidence/source URLs."""
    if not value or value == "#":
        return True

    # Allow local evidence image endpoint.
    if value.startswith(settings.EVIDENCE_URL_PREFIX):
        return True

    # Block protocol-relative URLs like //evil.com
    if value.startswith("//"):
        return False

    parsed = urlparse(value)
    return parsed.scheme in ("http", "https")


def _sanitize_evidence_item(item: dict) -> dict:
    """Sanitize evidence payload before sending to frontend."""
    cleaned = dict(item)

    link = str(cleaned.get("link", "") or "")
    url = str(cleaned.get("url", "") or "")

    if not _is_safe_url(link):
        cleaned["link"] = "#"

    if url and not _is_safe_url(url):
        cleaned["url"] = ""

    # Mask PII if extracted table text is sent as evidence.
    if cleaned.get("content"):
        cleaned["content"] = mask_pii(str(cleaned["content"]))

    return cleaned


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


# ── Evidence selection helpers ──────────────────────────────────────────
def _normalize_text(value: str) -> str:
    """Lowercase text and collapse spaces for matching."""
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _contains_any(text: str, phrases: list[str]) -> bool:
    text = _normalize_text(text)
    return any(phrase in text for phrase in phrases)


def _is_low_value_evidence(item: dict) -> bool:
    """Filter obvious non-helpful evidence such as TOC/control pages."""
    title = _normalize_text(str(item.get("title", "")))
    source = _normalize_text(str(item.get("source", "")))
    content = _normalize_text(str(item.get("content", "")))

    combined = f"{title} {source} {content}"

    low_value_phrases = [
        "table of content",
        "table of contents",
        "controlled circulation",
        "document preparation",
        "recommendation",
        "approval",
        "issue no",
        "revision no",
        "internal use only",
        "document control",
        "signature",
        "cover page",
        "page intentionally left blank",
    ]

    return _contains_any(combined, low_value_phrases)


def _score_evidence_item(answer_text: str, item: dict) -> int:
    """Score evidence relevance based on answer keywords and evidence content."""
    answer = _normalize_text(answer_text)

    title = _normalize_text(str(item.get("title", "")))
    source = _normalize_text(str(item.get("source", "")))
    content = _normalize_text(str(item.get("content", "")))

    haystack = f"{title} {source} {content}"

    score = 0

    if item.get("type") in ("image", "table"):
        score += 2

    keyword_groups = [
        ["performance management process", "process", "flowchart", "illustration", "diagram"],
        ["grievance", "committee"],
        ["overseas", "leave", "duration"],
        ["rating", "kpi", "competency", "scale"],
        ["weightage", "appraisal", "component"],
        ["medical", "benefit"],
        ["unauthorized absence", "summary of actions"],
        ["leave categories"],
    ]

    for group in keyword_groups:
        hits = sum(1 for word in group if word in answer and word in haystack)
        if hits >= 2:
            score += 8
        elif hits == 1:
            score += 3

    answer_words = {
        word for word in re.findall(r"[a-zA-Z]{4,}", answer)
        if word not in {
            "this", "that", "with", "from", "have", "your", "what",
            "when", "where", "which", "about", "would", "should",
            "their", "there", "these", "those", "into", "them",
            "performance", "management",
        }
    }

    haystack_words = set(re.findall(r"[a-zA-Z]{4,}", haystack))
    overlap = answer_words.intersection(haystack_words)
    score += min(len(overlap), 6)

    if "process" in answer or "flowchart" in answer or "diagram" in answer:
        if any(word in haystack for word in ["illustration", "flowchart", "diagram", "process"]):
            score += 10

    if not title and not content:
        score -= 2

    if _is_low_value_evidence(item):
        score -= 100

    page = item.get("page")
    if isinstance(page, int) and page > 0:
        score += 1

    return score


def _select_best_evidence(answer_text: str, evidence_items: list[dict]) -> list[dict]:
    """Keep only the single most relevant evidence item.

    Prefer cropped image evidence over raw extracted table text because
    cropped images are cleaner and closer to what users expect.
    """
    if not evidence_items:
        return []

    filtered = [item for item in evidence_items if not _is_low_value_evidence(item)]
    if not filtered:
        return []

    # Prefer cropped image evidence first.
    image_items = [
        item for item in filtered
        if item.get("type") == "image" and item.get("url")
    ]

    if image_items:
        ranked_images = sorted(
            image_items,
            key=lambda item: _score_evidence_item(answer_text, item),
            reverse=True,
        )
        return ranked_images[:1]

    # If no image evidence exists, do not show raw extracted text as evidence.
    # The Sources section is enough for text-only answers.
    return []


def _extract_used_source_keys(answer_text: str) -> tuple[set[str], set[str]]:
    """Extract source names and URLs from the final Sources section."""
    if not answer_text:
        return set(), set()

    parts = re.split(r"\*{0,2}Sources:\*{0,2}", answer_text, flags=re.IGNORECASE)
    if len(parts) < 2:
        return set(), set()

    sources_part = parts[-1]
    matches = re.finditer(r"\[(.*?)\]\((.*?)\)", sources_part)

    source_names: set[str] = set()
    source_urls: set[str] = set()

    for match in matches:
        name = (match.group(1) or "").strip().lower()
        url = (match.group(2) or "").strip().lower()

        if name:
            source_names.add(name)
        if url:
            source_urls.add(url)

    return source_names, source_urls


def _filter_evidence_for_answer(answer_text: str, evidence_items: list[dict]) -> list[dict]:
    """Keep only the most relevant evidence connected to sources used in the final answer."""
    if not evidence_items:
        return []

    # If the final answer has no Sources section, don't show evidence.
    if not re.search(r"\*{0,2}Sources:\*{0,2}", answer_text or "", flags=re.IGNORECASE):
        return []

    source_names, source_urls = _extract_used_source_keys(answer_text)

    # First keep only evidence from actually cited sources.
    if source_names or source_urls:
        source_filtered: list[dict] = []

        for item in evidence_items:
            source = str(item.get("source", "")).strip().lower()
            link = str(item.get("link", "")).strip().lower()

            if source in source_names or link in source_urls:
                source_filtered.append(item)
    else:
        source_filtered = evidence_items

    # Then select only the best relevant evidence item(s).
    return _select_best_evidence(answer_text, source_filtered)


def _build_evidence_stream_chunk(answer_text: str, thread_id: str) -> str:
    """Build hidden evidence JSON for the frontend, if relevant evidence exists."""
    evidence_items = consume_thread_evidence(
        thread_id,
        max_items=settings.EVIDENCE_MAX_ITEMS_PER_ANSWER,
    )

    evidence_items = _filter_evidence_for_answer(answer_text, evidence_items)
    evidence_items = [_sanitize_evidence_item(item) for item in evidence_items]

    if not evidence_items:
        return ""

    payload = {
        "items": evidence_items,
    }

    return (
        "\n\n[[EVIDENCE_JSON]]"
        + json.dumps(payload, ensure_ascii=False)
        + "[[/EVIDENCE_JSON]]"
    )


@router.post("")
async def chat(request: ChatRequest):
    """Handle an incoming chat message from the frontend with streaming."""
    try:
        builder_fn = get_agent_builder(request.agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    async def event_generator() -> AsyncGenerator[str, None]:
        clear_thread_evidence(request.thread_id)
        # Skip masking for product agents so SKU/model numbers survive.
        if request.agent_id in PII_MASK_EXEMPT_AGENTS:
            safe_user_message = request.message
        else:
            safe_user_message = mask_pii(request.message)
        # Tracked outside the try so the error handler can tell whether a
        # partial answer was already streamed before the failure.
        streamed_any_text = False
        try:
            # --- 2. Initialize Langfuse Handler (Empty in v3+) ---
            langfuse_handler = CallbackHandler()

            # --- 3. Inject callbacks and Langfuse metadata into the config ---
            config = {
                "configurable": {"thread_id": request.thread_id},
                "callbacks": [langfuse_handler],
                "metadata": {
                    "langfuse_session_id": request.thread_id,
                    "langfuse_user_id": request.user_id,
                    "langfuse_tags": [request.agent_id, "prod"]
                }
            }

            # Record who is behind this session (id + display name) so the admin
            # panel can attribute sessions and agents can personalize by name.
            record_session(
                agent_id=request.agent_id,
                thread_id=request.thread_id,
                user_id=request.user_id,
                user_name=request.user_name,
                department=request.department,
                job_title=request.job_title,
            )

            # ── Run guardrail classifier FIRST ──────────────────────────
            # gpt-4.1-nano is ~100-200ms, so this adds minimal latency
            # and lets us pass real sentiment into the agent state.
            guardrail = await classify_intent(safe_user_message)

            if guardrail.action == "BLOCK":
                logger.info(f"Guardrail BLOCK | reason={guardrail.reason}")
                # Save the blocked exchange to chat history
                try:
                    # Reuse the cached, pre-compiled graph bound to a long-lived pool.
                    graph = await get_compiled_async_graph(request.agent_id)
                    blocked_state = {
                        "messages": [
                            HumanMessage(content=safe_user_message),
                            AIMessage(content=BLOCK_MESSAGE),
                        ],
                        "agent_id": request.agent_id,
                        "user_id": request.user_id,
                        "user_name": request.user_name or request.user_id,
                        "department": request.department,
                        "job_title": request.job_title,
                        "thread_id": request.thread_id,
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
                "messages": [("user", safe_user_message)],
                "agent_id": request.agent_id,
                "user_id": request.user_id,
                "user_name": request.user_name or request.user_id,
                "thread_id": request.thread_id,
                "form_slots": {},
                "next_node": "",
                "sentiment": guardrail.sentiment,
            }

            # Reuse the cached, pre-compiled graph bound to a long-lived pool.
            graph = await get_compiled_async_graph(request.agent_id)

            streamed_response_text = ""

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

            # --- 4. The `config` object here now contains the Langfuse callbacks ---
            async for event in graph.astream_events(state, config, version="v2"):
                # ── Extract tokens from stream events ────────
                kind = event["event"]

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
                        streamed_response_text += text
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
                                streamed_response_text += text
                                yield text
                                break

            # ── Visual/table evidence ───────────────────────────────
            # Stream a hidden evidence block (cropped PDF images/tables)
            # after the answer. The frontend parses and renders it as a
            # "Relevant Evidence" section.
            evidence_chunk = _build_evidence_stream_chunk(
                streamed_response_text,
                request.thread_id,
            )

            if evidence_chunk:
                yield evidence_chunk

        except Exception as exc:
            import traceback
            error_details = traceback.format_exc()
            logger.error(f"Streaming error: {exc}\n{error_details}")

            # Show a friendly message instead of leaking raw provider errors
            # (quota exhausted, rate limits, etc.) to the user.
            if _is_quota_error(exc):
                user_message = BUSY_MESSAGE
            else:
                user_message = GENERIC_ERROR_MESSAGE

            # If we already streamed part of an answer, separate the notice.
            if streamed_any_text:
                yield "\n\n"
            yield user_message

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/{agent_id}/{thread_id}")
async def get_history(agent_id: str, thread_id: str):
    """Retrieve the chat history for a specific session."""
    try:
        builder_fn = get_agent_builder(agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    config = {"configurable": {"thread_id": thread_id}}

    try:
        graph = get_compiled_sync_graph(agent_id)

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
    
    