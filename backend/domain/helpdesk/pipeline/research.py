"""
Research workflow layer 1: solved tickets -> satisfaction check.

research_agent checks previously-solved tickets before ever hitting the
knowledge base; satisfaction_handler processes the user's reply once a
solved-ticket answer has been presented. See kb_search.py for the next
layer (KB search) this hands off to.
"""

import re
from typing import Literal

from langchain_core.messages import AIMessage

from domain.state import AgentState
from domain.helpdesk.pipeline.helpers import (
    llm,
    _message_to_text,
    _latest_user_message,
)
from domain.helpdesk.prompts import (
    RESEARCH_SYSTEM_PROMPT,
    SATISFACTION_CLOSING_SYSTEM_PROMPT,
    SELF_OR_HUMAN_MORE_INFO_SYSTEM_PROMPT,
)
from domain.helpdesk.tools.helpdesk_tools import (
    solved_ticket_search_llm,
    solved_ticket_verdict_llm,
)


# Layer 1 of the research branch: checks previously-solved tickets before
# hitting the knowledge base. Also the re-entry point for the mid-flow
# phases below, which it just forwards on without calling the LLM.
#
# Turn 1 — searches solved tickets.
# Turn 2 — reads the search result and calls present_solved_answer with
# matched=true/false (a tool call, never plain text, so a "no match"
# verdict can't leak to the user before this function decides what to do).
async def research_agent(state: AgentState) -> dict:
    """Entry point for research: a fresh question, or a continuation of a
    multi-turn phase."""
    research_phase = state.get("helpdesk_research_phase", "")
    ticket_phase = state.get("helpdesk_ticket_phase", "")

    if research_phase == "awaiting_satisfaction":
        print(
            "[research_agent] awaiting satisfaction response → dispatching to satisfaction_handler"
        )
        return {}
    if ticket_phase in (
        "awaiting_self_or_human",
        "awaiting_category_confirmation",
        "awaiting_final_confirmation",
        "awaiting_category_clarification",
    ):
        print(
            f"[research_agent] ticket_phase={ticket_phase!r} → forwarding to kb_search_agent"
        )
        return {}
    if ticket_phase == "awaiting_retry_clarification":
        print(
            "[research_agent] awaiting retry clarification → dispatching to kb_search_agent"
        )
        return {
            "helpdesk_research_phase": "no_solved_match",
            "helpdesk_original_query": _latest_user_message(state),
            "helpdesk_ticket_phase": "",
        }

    user_message = _latest_user_message(state)
    user_id = state.get("user_id", "anonymous")
    messages = state.get("messages", [])

    print(f"[research_agent] user_id={user_id!r} | messages={len(messages)}")

    system_prompt = {"role": "system", "content": RESEARCH_SYSTEM_PROMPT}

    # A ToolMessage as the last message means the search already ran —
    # this is Turn 2, not a fresh question.
    last_message = messages[-1] if messages else None
    search_already_ran = getattr(last_message, "type", "") == "tool"

    if not search_already_ran:
        response = await solved_ticket_search_llm.ainvoke([system_prompt, *messages])
        print(f"[research_agent] Turn 1 tool_calls={getattr(response, 'tool_calls', None)}")
        return {
            "messages": [response],
            "helpdesk_original_query": user_message,
        }

    response = await solved_ticket_verdict_llm.ainvoke([system_prompt, *messages])
    tool_calls = getattr(response, "tool_calls", None) or []
    decision = next(
        (tc for tc in tool_calls if tc.get("name") == "present_solved_answer"), None
    )

    if decision is not None:
        args = decision.get("args", {}) or {}
        matched = bool(args.get("matched"))
        answer = str(args.get("answer") or "").strip()

        if matched and answer:
            print("[research_agent] match found → asking for user satisfaction")
            return {
                "messages": [AIMessage(content=answer)],
                "helpdesk_research_phase": "awaiting_satisfaction",
                "helpdesk_original_query": user_message,
            }

        print("[research_agent] no match → will route to KB search")
        return {
            "helpdesk_research_phase": "no_solved_match",
            "helpdesk_original_query": user_message,
        }

    # Safety net: model ignored the tool-only instruction and replied with
    # plain text — don't trust it as a match, fail safe to no-match.
    print(
        "[research_agent] WARNING: verdict turn returned no present_solved_answer "
        f"call — reply={_message_to_text(response)[:100]!r}"
    )
    return {
        "helpdesk_research_phase": "no_solved_match",
        "helpdesk_original_query": user_message,
    }


def route_after_research(
    state: AgentState,
) -> Literal[
    "research_tools",
    "kb_search_agent",
    "satisfaction_handler",
    "__end__",
]:
    """After research_agent: execute a tool call, hand off to KB search,
    wait for a satisfaction reply, or end the turn."""
    research_phase = state.get("helpdesk_research_phase", "")

    if research_phase == "awaiting_satisfaction":
        last_msg = state["messages"][-1]
        if getattr(last_msg, "type", "") == "ai":
            print("[router] solved match found → ending (awaiting user satisfaction)")
            return "__end__"
        print("[router] user replied to satisfaction check → satisfaction_handler")
        return "satisfaction_handler"

    ticket_phase = state.get("helpdesk_ticket_phase", "")
    if ticket_phase in (
        "awaiting_self_or_human",
        "awaiting_category_confirmation",
        "awaiting_final_confirmation",
        "awaiting_category_clarification",
    ):
        print(f"[router] ticket_phase={ticket_phase!r} → forwarding to kb_search_agent")
        return "kb_search_agent"

    last_message = state["messages"][-1]
    if getattr(last_message, "tool_calls", None):
        print("[research_agent] tool call detected → running research_tools")
        return "research_tools"

    if research_phase == "no_solved_match":
        print("[router] no solved match → KB search")
        return "kb_search_agent"

    print("[router] ending")
    return "__end__"


# Fires when the user replies to "did that answer your question?". Pure
# keyword matching — no LLM call for the decision, only for the closing reply.
async def satisfaction_handler(state: AgentState) -> dict:
    """Handle the user's satisfaction response using keyword matching."""
    user_message = _latest_user_message(state)
    user_id = state.get("user_id", "anonymous")
    user_lower = user_message.lower().strip()
    print(f"[satisfaction_handler] user_id={user_id!r} response={user_message!r}")

    satisfied_signals = {
        "1",
        "yes",
        "yep",
        "yeah",
        "ok",
        "okay",
        "sure",
        "great",
        "perfect",
        "thanks",
        "thank",
        "solved",
        "fixed",
        "worked",
        "good",
        "clear",
        "helpful",
        "helped",
        "understood",
        "fine",
        "alright",
    }
    not_satisfied_signals = {
        "2",
        "no",
        "nope",
        "not",
        "nah",
        "still",
        "issue",
        "problem",
        "wrong",
        "incorrect",
        "doesn't",
        "didn't",
        "don't",
        "isn't",
        "haven't",
        "hasn't",
        "again",
        "another",
    }
    # Option 3 ("I'd like to know more information") — same signal set as
    # self_or_human_handler's more_info_signals (self_or_human.py), which
    # handles the equivalent option after a KB-search answer.
    more_info_signals = {
        "3",
        "more",
        "info",
        "information",
    }

    words = set(re.sub(r"[^\w\s]", "", user_lower).split())
    is_not_satisfied = bool(words & not_satisfied_signals)
    is_satisfied = bool(words & satisfied_signals) and not is_not_satisfied
    is_more_info = (
        bool(words & more_info_signals) and not is_not_satisfied and not is_satisfied
    )

    if is_more_info:
        print(
            "[satisfaction_handler] user chose option 3 (know more) → "
            "asking what topic, then awaiting a fresh KB query"
        )
        response = await llm.ainvoke([
            {"role": "system", "content": SELF_OR_HUMAN_MORE_INFO_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ])
        return {
            "messages": [AIMessage(content=_message_to_text(response))],
            "helpdesk_research_phase": "",
            # Reuses kb_search.py's vague-query pre-check phase — the
            # user's next reply becomes a fresh KB query automatically
            # (same mechanism self_or_human_handler's option 3 uses).
            "helpdesk_ticket_phase": "awaiting_retry_clarification",
            "helpdesk_retry_count": 0,
        }

    if is_not_satisfied or not is_satisfied:
        print("[satisfaction_handler] user not satisfied → proceeding to KB search")
        return {"helpdesk_research_phase": "not_satisfied"}

    print("[satisfaction_handler] user satisfied → closing conversation")
    closing_messages = [
        {"role": "system", "content": SATISFACTION_CLOSING_SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]
    closing_response = await llm.ainvoke(closing_messages)
    closing_reply = _message_to_text(closing_response)

    return {
        "messages": [AIMessage(content=closing_reply)],
        "helpdesk_research_phase": "",
        "helpdesk_original_query": "",
    }


def route_after_satisfaction(
    state: AgentState,
) -> Literal["kb_search_agent", "__end__"]:
    """After satisfaction check: not satisfied → KB search, satisfied or
    awaiting a "what would you like to know" reply → END."""
    phase = state.get("helpdesk_research_phase", "")
    if phase == "not_satisfied":
        print("[router] not satisfied → KB search")
        return "kb_search_agent"
    if state.get("helpdesk_ticket_phase", "") == "awaiting_retry_clarification":
        print("[router] asked what to know more about → ending (awaiting reply)")
        return "__end__"
    print("[router] satisfied → ending")
    return "__end__"
