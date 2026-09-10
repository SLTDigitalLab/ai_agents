"""
Research workflow layer 2: knowledge-base search + answer validation.

kb_search_agent searches the Qdrant knowledge base once no solved ticket
matched (see research.py for layer 1); validate_kb_answer then decides
whether that answer was good enough, needs clarification, or means "not in
the KB — make a ticket" (handed off to duplicates.check_duplicates).
"""

from typing import Literal

from langchain_core.messages import AIMessage

from domain.state import AgentState
from domain.helpdesk.pipeline.helpers import (
    llm,
    _message_to_text,
    _latest_user_message,
    _continues_prior_reply,
    _is_query_too_vague,
)
from domain.helpdesk.prompts import kb_search_system_prompt, vague_query_clarification_system_prompt
from domain.helpdesk.tools.helpdesk_tools import kb_llm, kb_search_llm


# Layer 2: searches the Qdrant knowledge base and answers from the
# results. Reached when no solved ticket matched. Also the re-entry point
# for the mid-flow phases below, which it just forwards on.
#
# Turn 1 — LLM calls search_knowledge_base -> kb_search_tools.
# Turn 2 — LLM reads the tool result and produces the final answer.
async def kb_search_agent(state: AgentState) -> dict:
    """Entry point for the KB-search-and-beyond layer."""
    ticket_phase = state.get("helpdesk_ticket_phase", "")
    if ticket_phase in (
        "awaiting_self_or_human",
        "awaiting_category_confirmation",
        "awaiting_final_confirmation",
        "awaiting_category_clarification",
    ):
        print(f"[kb_search_agent] ticket_phase={ticket_phase!r} → dispatching onward")
        return {}

    original_query = state.get("helpdesk_original_query", "") or _latest_user_message(
        state
    )
    agent_id = state.get("agent_id", "helpdesk")
    user_id = state.get("user_id", "anonymous")
    messages = state.get("messages", [])

    print(
        f"[kb_search_agent] user_id={user_id!r} agent_id={agent_id!r} query={original_query!r}"
    )

    system_prompt = {
        "role": "system",
        "content": kb_search_system_prompt(original_query),
    }

    # Only a ToolMessage from search_knowledge_base specifically means the
    # KB search already ran — the last message could also be an unrelated
    # ToolMessage from research_agent's solved-ticket search.
    last_message = messages[-1] if messages else None
    kb_search_already_ran = (
        getattr(last_message, "type", "") == "tool"
        and getattr(last_message, "name", "") == "search_knowledge_base"
    )

    if not kb_search_already_ran and _is_query_too_vague(original_query):
        # Ask for details before even running the KB search, so the user
        # doesn't see a "not found" reply immediately followed by a
        # contradicting "please give more details" (observed live 2026-08-11).
        retry_count = state.get("helpdesk_retry_count", 0)
        if retry_count < 2:
            print(
                "[kb_search_agent] vague query pre-check → awaiting_retry_clarification "
                f"({original_query!r})"
            )
            response = await llm.ainvoke([
                {
                    "role": "system",
                    "content": vague_query_clarification_system_prompt(
                        original_query, continuation=False
                    ),
                },
            ])
            return {
                "messages": [AIMessage(content=_message_to_text(response))],
                "helpdesk_ticket_phase": "awaiting_retry_clarification",
                "helpdesk_retry_count": retry_count + 1,
            }
        print("[kb_search_agent] vague query pre-check → creating_ticket (max retries)")
        return {
            "helpdesk_ticket_phase": "creating_ticket",
            "helpdesk_retry_count": 0,
        }

    if not kb_search_already_ran:
        response = await kb_search_llm.ainvoke([system_prompt, *messages])
        print(f"[kb_search_agent] Turn 1 tool_calls={getattr(response, 'tool_calls', None)}")
        return {"messages": [response]}

    response = await kb_llm.ainvoke([system_prompt, *messages])
    print(f"[kb_search_agent] reply={_message_to_text(response)[:100]!r}...")
    return {"messages": [response]}


def should_continue_kb_search(
    state: AgentState,
) -> Literal[
    "kb_search_tools",
    "validate_kb_answer",
    "self_or_human_handler",
    "confirm_category_handler",
    "category_clarification_handler",
    "check_duplicates",
    "__end__",
]:
    """After kb_search_agent: execute a tool call, validate the answer, or
    dispatch to whichever mid-flow handler is active."""
    ticket_phase = state.get("helpdesk_ticket_phase", "")
    if ticket_phase == "awaiting_self_or_human":
        print("[router] awaiting self-or-human → self_or_human_handler")
        return "self_or_human_handler"
    if ticket_phase in ("awaiting_category_confirmation", "awaiting_final_confirmation"):
        print(f"[router] {ticket_phase} → confirm_category_handler")
        return "confirm_category_handler"
    if ticket_phase == "awaiting_category_clarification":
        print("[router] awaiting category clarification → category_clarification_handler")
        return "category_clarification_handler"
    if ticket_phase == "awaiting_retry_clarification":
        print("[router] vague-query pre-check asked for details → ending (awaiting reply)")
        return "__end__"
    if ticket_phase == "creating_ticket":
        print("[router] vague-query pre-check hit retry cap → checking duplicates")
        return "check_duplicates"

    last_message = state["messages"][-1]
    if getattr(last_message, "tool_calls", None):
        print("[kb_search_agent] tool call detected → running kb_search_tools")
        return "kb_search_tools"
    print("[kb_search_agent] no tool call → validating KB answer")
    return "validate_kb_answer"


# Decides, via string matching on kb_search_agent's own reply (no LLM call
# in the common case), whether the answer was good enough, needs
# clarification, or means "not in the KB — make a ticket".
async def validate_kb_answer(state: AgentState) -> dict:
    """Route after KB search using Python heuristics instead of an LLM call."""
    messages_list = state.get("messages", [])
    original_query = state.get("helpdesk_original_query", "")

    tool_result = ""
    ai_answer = ""
    for msg in reversed(messages_list):
        if not tool_result and getattr(msg, "type", "") == "tool":
            tool_result = _message_to_text(msg)
        if not ai_answer and getattr(msg, "type", "") == "ai":
            ai_answer = _message_to_text(msg)
        if tool_result and ai_answer:
            break

    ai_lower = ai_answer.lower()

    # NOTE: kb_search_system_prompt's "not found" branch must start with
    # "wasn't able to find specific information about that" — these
    # phrases are matched against that exact wording.
    no_info_phrases = [
        "wasn't able to find",
        "unable to find",
        "can't find",
        "cannot find",
        "no specific information",
        "no relevant information",
        "no information found",
        "couldn't find",
        "could not find",
        "no information about",
    ]
    # NOTE: option 3's wording in kb_search_system_prompt's "found" branch
    # ("I'd like to know more information") must never collide with these.
    clarification_phrases = [
        "more details",
        "more specific",
        "could you provide",
        "provide more",
        "clarify",
        "more information about",
        "tell me more",
    ]

    if any(p in ai_lower for p in no_info_phrases) or len(tool_result.strip()) < 50:
        # Safety net: kb_search_agent already checks this before searching,
        # so this is normally unreachable — kept in case a future caller
        # skips that pre-check.
        if _is_query_too_vague(original_query):
            retry_count = state.get("helpdesk_retry_count", 0)
            if retry_count < 2:
                print(
                    "[validate_kb_answer] → awaiting_retry_clarification "
                    f"(vague query override: {original_query!r})"
                )
                # Via the LLM (not a hardcoded string) so it actually
                # streams; continuation=True since kb_search_agent's reply
                # already streamed earlier this turn.
                response = await llm.ainvoke([
                    {
                        "role": "system",
                        "content": vague_query_clarification_system_prompt(
                            original_query, continuation=_continues_prior_reply(state)
                        ),
                    },
                ])
                return {
                    "messages": [AIMessage(content=_message_to_text(response))],
                    "helpdesk_ticket_phase": "awaiting_retry_clarification",
                    "helpdesk_retry_count": retry_count + 1,
                }
            print("[validate_kb_answer] → creating_ticket (vague query, max retries)")
            return {
                "helpdesk_ticket_phase": "creating_ticket",
                "helpdesk_retry_count": 0,
            }
        print("[validate_kb_answer] → creating_ticket (not_in_kb heuristic)")
        return {"helpdesk_ticket_phase": "creating_ticket"}

    if any(p in ai_lower for p in clarification_phrases):
        retry_count = state.get("helpdesk_retry_count", 0)
        if retry_count >= 2:
            print("[validate_kb_answer] → creating_ticket (max retries)")
            return {
                "helpdesk_ticket_phase": "creating_ticket",
                "helpdesk_retry_count": 0,
            }
        print("[validate_kb_answer] → awaiting_retry_clarification")
        return {
            "helpdesk_ticket_phase": "awaiting_retry_clarification",
            "helpdesk_retry_count": retry_count + 1,
        }

    print("[validate_kb_answer] → awaiting_self_or_human (kb_valid heuristic)")
    return {"helpdesk_ticket_phase": "awaiting_self_or_human"}


def route_after_kb_validation(
    state: AgentState,
) -> Literal["check_duplicates", "__end__"]:
    """After validate_kb_answer: creating_ticket → check duplicates, else wait."""
    phase = state.get("helpdesk_ticket_phase", "")
    if phase == "creating_ticket":
        print("[router] not in KB → checking duplicates")
        return "check_duplicates"
    print("[router] KB answer presented → waiting for user choice")
    return "__end__"
