"""
Shared LLM client + small pure-Python helpers used across the helpdesk
pipeline (domain/helpdesk/pipeline/*.py).
"""

import re
from typing import Any, Literal

from domain.state import AgentState
from core.llm import get_chat_model

llm = get_chat_model()

# Tag for LLM calls whose output is internal-only (never shown to the
# user), so routers/chat.py's stream listener can filter them out.
INTERNAL_LLM_TAG = "helpdesk_internal_llm_call"


def _message_to_text(message: Any) -> str:
    """Extract plain text from a LangChain message or raw value."""
    content = getattr(message, "content", message)

    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and "text" in block:
                parts.append(str(block["text"]))
        return " ".join(part.strip() for part in parts if part).strip()

    if content is None:
        return ""

    return str(content).strip()


def _latest_user_message(state: AgentState) -> str:
    """Return the most recent user message from the graph state."""
    for message in reversed(state.get("messages", [])):
        if getattr(message, "type", "") in ("human", "user"):
            return _message_to_text(message)
    return ""


def _latest_ai_message(state: AgentState) -> str:
    """Return the most recent AI message from the graph state."""
    for message in reversed(state.get("messages", [])):
        if getattr(message, "type", "") == "ai":
            return _message_to_text(message)
    return ""


def _continues_prior_reply(state: AgentState) -> bool:
    """True if another node already replied earlier in this same turn
    (used to avoid an awkward run-on continuation in the streamed output)."""
    messages = state.get("messages", [])
    return bool(messages) and getattr(messages[-1], "type", "") == "ai"


_STANDALONE_GREETING_RE = re.compile(
    r"^(hi+|hello+|hey+|hiya|yo|good\s*(morning|afternoon|evening|day)|"
    r"thanks?( you| u)?|thank\s*you|bye+|goodbye|see\s*you( later)?|take\s*care)"
    r"[\s!.,]*$",
    re.IGNORECASE,
)


def _is_standalone_greeting(text: str) -> bool:
    """True only when the ENTIRE message is a bare greeting (e.g. "hi"),
    used to detect a mid-flow interruption without calling the LLM."""
    return bool(_STANDALONE_GREETING_RE.match(text.strip()))


_FOLLOWUP_QUESTION_RE = re.compile(
    r"\?|\b(what|how|why|when|where|which|who|explain|clarify|elaborate|"
    r"difference|meaning|means|example|instead|tell\s*me|more\s*(info|details|about))\b",
    re.IGNORECASE,
)

# Narrower than _FOLLOWUP_QUESTION_RE: no bare "?", so a short "yes?" still
# resolves as an answer instead of a follow-up question.
_STRONG_FOLLOWUP_QUESTION_RE = re.compile(
    r"\b(what|how|why|when|where|which|who|explain|clarify|elaborate|"
    r"difference|meaning|means|example|instead|tell\s*me|more\s*(info|details|about))\b",
    re.IGNORECASE,
)


def _looks_like_followup_question(text: str) -> bool:
    """True when the reply looks like a question rather than an answer to
    a pending 1/2/3-style choice."""
    return bool(_FOLLOWUP_QUESTION_RE.search(text.strip()))


# Word-count floor for "too vague to search or ticket on" (e.g. "issue",
# "not working"). Tuned from eval data — see helpdesk-category-accuracy-gap
# project memory for the numbers behind 5.
_VAGUE_QUERY_MIN_WORDS = 5


def _is_query_too_vague(query: str) -> bool:
    """True when the user's issue description has fewer than
    ``_VAGUE_QUERY_MIN_WORDS`` words — too thin to search or ticket on."""
    return len(query.strip().split()) < _VAGUE_QUERY_MIN_WORDS


def _normalize_message_type(
    raw: str,
) -> Literal["greeting", "research", "ticket_related"]:
    """Map the classifier LLM's free-text label to one of the 3 routing
    categories, defaulting to "research" for anything unrecognized."""
    normalized = raw.strip().lower()
    if "greeting" in normalized:
        return "greeting"
    if "ticket" in normalized:
        return "ticket_related"
    if "research" in normalized:
        return "research"
    print(f"[classifier] WARNING: unrecognised label {raw!r}, defaulting to 'research'")
    return "research"
