"""
Self-or-human handler: fires when the user replies to KB search's
"1. This solves it / 2. Create a ticket / 3. I'd like to know more" question.
"""

import asyncio
import re
from typing import Literal

from langchain_core.messages import AIMessage

from domain.state import AgentState
from domain.helpdesk.pipeline.helpers import (
    llm,
    _message_to_text,
    _latest_user_message,
    _latest_ai_message,
    _STRONG_FOLLOWUP_QUESTION_RE,
    _looks_like_followup_question,
    _is_query_too_vague,
)
from domain.helpdesk.prompts import (
    SELF_OR_HUMAN_TICKET_SYSTEM_PROMPT,
    SELF_OR_HUMAN_SELF_SYSTEM_PROMPT,
    SELF_OR_HUMAN_MORE_INFO_SYSTEM_PROMPT,
)
from services.helpdesk_tickets import create_solved_ticket


# Fires when the user replies to "1. This solves it / 2. Create a ticket /
# 3. know more". Keyword matching decides the branch; an LLM only
# generates the user-facing reply text.
async def self_or_human_handler(state: AgentState) -> dict:
    """Handle the user's choice using keyword detection + streaming LLM response."""
    user_message = _latest_user_message(state)
    user_lower = re.sub(r"[^\w\s]", "", user_message.lower()).strip()
    words = set(user_lower.split())

    ticket_signals = {
        "2",
        "no",
        "ticket",
        "support",
        "create",
        "raise",
        "human",
        "team",
        "agent",
        "help",
    }
    self_signals = {
        "1",
        "yes",
        "solved",
        "solves",
        "resolved",
        "resolves",
        "myself",
        "self",
        "own",
        "handle",
        "manage",
        "got",
        "understand",
        "ill",
    }
    # "information" doesn't match _STRONG_FOLLOWUP_QUESTION_RE's "more info"
    # pattern, so option 3 needs its own explicit signal set.
    more_info_signals = {
        "3",
        "more",
        "info",
        "information",
    }

    # Checked before the keyword scan: a genuine question like "can you
    # help me understand why..." hits both a ticket_signal ("help") and a
    # self_signal ("understand"), so it'd otherwise look like a clear answer.
    if _STRONG_FOLLOWUP_QUESTION_RE.search(user_message):
        print(
            "[self_or_human_handler] follow-up question detected (strong signal, "
            "overriding any keyword hits) → routing back to a fresh KB search"
        )
        return {
            "helpdesk_ticket_phase": "",
            "helpdesk_research_phase": "no_solved_match",
            "helpdesk_original_query": user_message,
            "helpdesk_retry_count": 0,
        }

    wants_ticket = bool(words & ticket_signals)
    wants_self = bool(words & self_signals) and not wants_ticket
    wants_more_info = (
        bool(words & more_info_signals) and not wants_ticket and not wants_self
    )
    ambiguous = not wants_ticket and not wants_self and not wants_more_info

    print(
        f"[self_or_human_handler] wants_ticket={wants_ticket} wants_self={wants_self} "
        f"wants_more_info={wants_more_info} ambiguous={ambiguous}"
    )

    if wants_more_info:
        print(
            "[self_or_human_handler] user chose option 3 (know more) → "
            "asking what topic, then awaiting a fresh KB query"
        )
        response = await llm.ainvoke([
            {"role": "system", "content": SELF_OR_HUMAN_MORE_INFO_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ])
        return {
            "messages": [AIMessage(content=_message_to_text(response))],
            # Reuses kb_search.py's vague-query pre-check phase — the
            # user's next reply becomes a fresh KB query automatically.
            "helpdesk_ticket_phase": "awaiting_retry_clarification",
            "helpdesk_retry_count": 0,
        }

    if ambiguous and _looks_like_followup_question(user_message):
        print(
            "[self_or_human_handler] follow-up question detected → "
            "routing back to a fresh KB search"
        )
        return {
            "helpdesk_ticket_phase": "",
            "helpdesk_research_phase": "no_solved_match",
            "helpdesk_original_query": user_message,
            "helpdesk_retry_count": 0,
        }

    if ambiguous:
        retry_count = state.get("helpdesk_retry_count", 0)
        if retry_count < 2:
            print(
                "[self_or_human_handler] unrecognized reply "
                f"(attempt {retry_count + 1}) → re-prompting for 1/2/3 choice"
            )
            return {
                "messages": [AIMessage(content=(
                    "Sorry, I didn't quite catch that. Please reply with:\n"
                    "1. ✅ **Yes, this solves my issue**\n"
                    "2. 🎫 **No, please create a support ticket for further help**\n"
                    "3. ℹ️ **I'd like to know more information**"
                ))],
                "helpdesk_ticket_phase": "awaiting_self_or_human",
                "helpdesk_retry_count": retry_count + 1,
            }
        print(
            "[self_or_human_handler] unrecognized reply after max retries "
            "→ defaulting to ticket creation"
        )

    if wants_ticket or ambiguous:
        response = await llm.ainvoke([
            {"role": "system", "content": SELF_OR_HUMAN_TICKET_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ])
        result = {
            "messages": [AIMessage(content=_message_to_text(response))],
            "helpdesk_ticket_phase": "creating_ticket",
            "helpdesk_retry_count": 0,
        }
        # Refresh the query to what the user just said, not the stale one
        # from before the KB search's 1/2 question (bug found 2026-08-11).
        # Guarded so a bare "2" doesn't overwrite a good earlier description.
        if not _is_query_too_vague(user_message):
            result["helpdesk_original_query"] = user_message
        return result

    # User confirmed the KB answer resolved it — save it as a solved
    # ticket so research_agent can shortcut straight to it next time.
    original_query = state.get("helpdesk_original_query", "") or _latest_user_message(
        state
    )
    kb_answer = _latest_ai_message(state)
    # Strip the trailing "1/2/3" choice menu before saving. Anchored on
    # option 3's fixed wording ("I'd like to know more information"), not
    # the intro sentence above it, since that intro is free to reword (see
    # research_prompts.py's kb_search_system_prompt() for why).
    lower_answer = kb_answer.lower()
    menu_anchor_idx = lower_answer.find("i'd like to know more information")
    if menu_anchor_idx != -1:
        paragraph_break_idx = kb_answer.rfind("\n\n", 0, menu_anchor_idx)
        if paragraph_break_idx != -1:
            kb_answer = kb_answer[:paragraph_break_idx].strip()

    if original_query and kb_answer:
        try:
            # Off the event loop — see duplicates.py's check_duplicates()
            # for why a synchronous psycopg call here would stall the
            # whole process, not just this request.
            await asyncio.to_thread(
                create_solved_ticket, requirements=original_query, answer=kb_answer
            )
            print(
                f"[self_or_human_handler] saved solved ticket: "
                f"query={original_query[:60]!r}"
            )
        except Exception as exc:
            print(f"[self_or_human_handler] ERROR saving solved ticket: {exc}")

    response = await llm.ainvoke([
        {"role": "system", "content": SELF_OR_HUMAN_SELF_SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ])
    return {
        "messages": [AIMessage(content=_message_to_text(response))],
        "helpdesk_ticket_phase": "",
        "helpdesk_research_phase": "",
        "helpdesk_original_query": "",
        "helpdesk_retry_count": 0,
        "helpdesk_category_clarify_count": 0,
    }


def route_after_self_or_human(
    state: AgentState,
) -> Literal["check_duplicates", "kb_search_agent", "__end__"]:
    """After self_or_human_handler: ticket → check duplicates,
    follow-up question → fresh KB search, self/re-prompt/know-more → END."""
    phase = state.get("helpdesk_ticket_phase", "")
    if phase == "creating_ticket":
        print("[router] user chose ticket → checking duplicates")
        return "check_duplicates"
    if phase == "awaiting_self_or_human":
        print("[router] unrecognized reply → re-prompted, waiting for user")
        return "__end__"
    if phase == "awaiting_retry_clarification":
        print("[router] asked what to know more about → ending (awaiting reply)")
        return "__end__"
    if state.get("helpdesk_research_phase", "") == "no_solved_match":
        print("[router] follow-up question → routing to a fresh KB search")
        return "kb_search_agent"
    print("[router] user self-resolved → ending")
    return "__end__"
