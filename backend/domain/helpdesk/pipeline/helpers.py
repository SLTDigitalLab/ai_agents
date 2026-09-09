"""
Shared LLM client + small pure-Python helpers used across every stage of
the helpdesk pipeline (domain/helpdesk/pipeline/*.py). No other pipeline
module is imported here — this is the base module everything else builds on.
"""

import re
from typing import Any, Literal

from domain.state import AgentState
from core.llm import get_chat_model

llm = get_chat_model()

# Tag applied to LLM calls whose output is internal-only (never shown to
# the user as-is — e.g. the raw "**Category:** X" picks inside
# category_classification.classify_ticket_category_pipeline()'s
# hierarchical classifier). routers/chat.py's astream_events listener
# checks for this tag to keep these calls out of the user-facing SSE
# stream, since node-name-based suppression alone can't distinguish them
# from the legitimate final reply an LLM call in the SAME graph node
# (draft_ticket / category_clarification_handler, ticket_draft.py)
# produces later in that same node call. See
# category_classification._classify_hierarchical()'s docstring for the bug
# this fixes.
INTERNAL_LLM_TAG = "helpdesk_internal_llm_call"


# [HELPER] Turns any LangChain message (or plain value) into a plain string.
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


# [HELPER] Walks state["messages"] backwards to find the most recent human message.
def _latest_user_message(state: AgentState) -> str:
    """Return the most recent user message from the graph state."""
    for message in reversed(state.get("messages", [])):
        if getattr(message, "type", "") in ("human", "user"):
            return _message_to_text(message)
    return ""


# [HELPER] Walks state["messages"] backwards to find the most recent AI message.
def _latest_ai_message(state: AgentState) -> str:
    """Return the most recent AI message from the graph state."""
    for message in reversed(state.get("messages", [])):
        if getattr(message, "type", "") == "ai":
            return _message_to_text(message)
    return ""


# [HELPER] Used by nodes that may fire mid-turn after another node already
# replied (e.g. draft_ticket after check_duplicates), so they can avoid
# rendering as an awkward run-on continuation in the streamed output.
def _continues_prior_reply(state: AgentState) -> bool:
    """True if a different node already sent a user-visible reply earlier in
    this same turn (the newest message is an AI message, not the user's).

    A fresh turn always starts with the user's new message freshly appended
    last, so this is only True when this turn's graph run has already passed
    through another node that produced its own AIMessage — meaning the reply
    about to be generated would otherwise render as a run-on continuation of
    that earlier one in the streamed output.
    """
    messages = state.get("messages", [])
    return bool(messages) and getattr(messages[-1], "type", "") == "ai"


_STANDALONE_GREETING_RE = re.compile(
    r"^(hi+|hello+|hey+|hiya|yo|good\s*(morning|afternoon|evening|day)|"
    r"thanks?( you| u)?|thank\s*you|bye+|goodbye|see\s*you( later)?|take\s*care)"
    r"[\s!.,]*$",
    re.IGNORECASE,
)


# [HELPER] Regex check used by classify_message to detect "user said hi
# while a multi-turn flow was active" without calling the LLM.
def _is_standalone_greeting(text: str) -> bool:
    """True only when the ENTIRE message is a bare greeting/pleasantry.

    Used to detect a mid-flow interruption (user says "hi" instead of
    answering the pending question). Deliberately stricter than the LLM
    classifier used for fresh turns: an LLM given only the bare reply text
    with no surrounding context (e.g. "keep it", "no", "1") has nothing to
    anchor "no request attached" against and tends to mislabel it as a
    greeting, wrongly resetting an in-progress flow.
    """
    return bool(_STANDALONE_GREETING_RE.match(text.strip()))


_FOLLOWUP_QUESTION_RE = re.compile(
    r"\?|\b(what|how|why|when|where|which|who|explain|clarify|elaborate|"
    r"difference|meaning|means|example|instead|tell\s*me|more\s*(info|details|about))\b",
    re.IGNORECASE,
)

# [HELPER] Narrower than _FOLLOWUP_QUESTION_RE above: word-based question
# signals only, no bare "?". Used to let a genuine follow-up question win
# even when it also contains a yes/no/ticket keyword (see
# self_or_human.self_or_human_handler) — e.g. "can you help me understand
# why..." hits both "help" and "understand" but is unmistakably a question,
# not an answer. Deliberately excludes bare "?" here so a short reply like
# "yes?" still resolves as a yes/no answer via the keyword path instead.
_STRONG_FOLLOWUP_QUESTION_RE = re.compile(
    r"\b(what|how|why|when|where|which|who|explain|clarify|elaborate|"
    r"difference|meaning|means|example|instead|tell\s*me|more\s*(info|details|about))\b",
    re.IGNORECASE,
)


# [HELPER] Regex check used by self_or_human_handler to tell a genuine
# follow-up question apart from an unrecognized reply to its 1/2 prompt.
def _looks_like_followup_question(text: str) -> bool:
    """True when the reply looks like the user is asking something instead
    of answering the pending 1/2 choice.

    Without this, a genuine follow-up question (e.g. "how does the FTTH
    step work?") falls through as an unrecognized reply and gets re-prompted
    with "please reply 1 or 2" instead of actually being answered.
    """
    return bool(_FOLLOWUP_QUESTION_RE.search(text.strip()))


# [HELPER] Used by validate_kb_answer to catch queries too thin to ever
# usefully match the KB or be worth a ticket ("issue", "not working").
# A word-count floor, not an LLM judgment call — deliberately independent
# of whatever phrasing kb_search_agent's reply happened to use, so a model
# that says "not found" for a 1-word query can't skip straight to creating
# an empty ticket.
#
# Widened from 3 to 5 on 2026-08-09: measured on
# domain/helpdesk/data/pipeline_eval_200.xlsx (200 representative tickets), <5-word
# messages scored 40.6% category accuracy vs. 48.5% for >=5 words — an
# ~8-point gap, notably stronger and better-supported (101 vs. 32 tickets)
# than the old <3-word cutoff's ~4.6-point gap. Only affects tickets where
# the KB search ALSO already came back empty (see the call site below), so
# widening this doesn't add friction to short messages that get a good KB
# answer — only to ones already headed for an under-informed ticket.
_VAGUE_QUERY_MIN_WORDS = 5


def _is_query_too_vague(query: str) -> bool:
    """True when the user's issue description has fewer than
    ``_VAGUE_QUERY_MIN_WORDS`` words — too thin to search or ticket on."""
    return len(query.strip().split()) < _VAGUE_QUERY_MIN_WORDS


# [HELPER] Maps the classifier LLM's free-text label to one of the 3 fixed
# routing categories, defaulting to "research" for anything unrecognized.
def _normalize_message_type(
    raw: str,
) -> Literal["greeting", "research", "ticket_related"]:
    normalized = raw.strip().lower()
    if "greeting" in normalized:
        return "greeting"
    if "ticket" in normalized:
        return "ticket_related"
    if "research" in normalized:
        return "research"
    print(f"[classifier] WARNING: unrecognised label {raw!r}, defaulting to 'research'")
    return "research"
