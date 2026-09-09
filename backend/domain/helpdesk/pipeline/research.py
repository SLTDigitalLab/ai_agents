"""
Research workflow layer 1: solved tickets → satisfaction check.

research_agent checks previously-solved tickets before ever hitting the
knowledge base; satisfaction_handler processes the user's reply once a
solved-ticket answer has been presented. See
domain/helpdesk/pipeline/graph.py's module docstring for how this fits into
the overall flow, and kb_search.py for the next layer (KB search) this
hands off to.
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
from domain.helpdesk.prompts import RESEARCH_SYSTEM_PROMPT, SATISFACTION_CLOSING_SYSTEM_PROMPT
from domain.helpdesk.tools.helpdesk_tools import (
    solved_ticket_search_llm,
    solved_ticket_verdict_llm,
)


# [NODE] Layer 1 of the research branch: checks previously-solved tickets
# before ever hitting the knowledge base. Runs twice per fresh question
# (search, then verdict — see the docstring below), and is also the
# re-entry point for the awaiting_satisfaction / awaiting_self_or_human /
# awaiting_category_confirmation / awaiting_retry_clarification phases,
# where it just forwards the turn on without calling the LLM.
async def research_agent(state: AgentState) -> dict:
    """
    Entry point for all research work: a fresh question, or a continuation
    of a multi-turn phase. research_agent only owns the solved-ticket +
    satisfaction layer — everything else (retry clarification, self-or-human,
    category confirmation) is forwarded to kb_search_agent untouched, which
    is the next layer's dispatcher. Phase continuations make no LLM call here.

    Turn 1 — solved_ticket_search_llm (only search_solved_tickets_tool bound)
             sees the user question → emits a search tool_call → graph
             routes to research_tools.
    Turn 2 — ToolNode has appended a ToolMessage with the search results.
             solved_ticket_verdict_llm (only present_solved_answer bound) is
             invoked instead — its verdict arrives as that tool call's
             structured args, never as free text, so a "no match" verdict
             can never stream to the user as visible text before this
             function gets to decide what to do with it:
               a) matched=true  → presents the answer + asks satisfaction → END
               b) matched=false → routes to kb_search_agent
    """
    research_phase = state.get("helpdesk_research_phase", "")
    ticket_phase = state.get("helpdesk_ticket_phase", "")

    # Phase continuations: defer to route_after_research, no LLM call here.
    if research_phase == "awaiting_satisfaction":
        print(
            "[research_agent] awaiting satisfaction response → dispatching to satisfaction_handler"
        )
        return {}
    if ticket_phase in (
        "awaiting_self_or_human",
        "awaiting_category_confirmation",
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

    # The last message being a ToolMessage means research_tools already ran
    # search_solved_tickets_tool earlier in this same mini-loop — so this is
    # Turn 2 (the verdict), not a fresh question.
    last_message = messages[-1] if messages else None
    search_already_ran = getattr(last_message, "type", "") == "tool"

    if not search_already_ran:
        # Turn 1 — only the search tool is bound, so the model can't jump
        # straight to a verdict before it has results to judge.
        response = await solved_ticket_search_llm.ainvoke([system_prompt, *messages])
        print(f"[research_agent] Turn 1 tool_calls={getattr(response, 'tool_calls', None)}")
        return {
            "messages": [response],
            "helpdesk_original_query": user_message,
        }

    # Turn 2 — only present_solved_answer is bound, so its tool-call args are
    # the ONLY channel available; there is no plain-text path for the model
    # to use here at all.
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

    # Safety net: the model ignored the tool-only instruction and replied
    # with plain text. That text may already have streamed live and can't be
    # un-sent, but we still must not trust unvalidated freeform text as a
    # "matched" answer — fail safe by treating it as no-match.
    print(
        "[research_agent] WARNING: verdict turn returned no present_solved_answer "
        f"call — reply={_message_to_text(response)[:100]!r}"
    )
    return {
        "helpdesk_research_phase": "no_solved_match",
        "helpdesk_original_query": user_message,
    }


# [ROUTER] Decides what happens after research_agent runs: execute its
# tool call, hand off to the KB layer, wait for the user's satisfaction
# reply, or end the turn.
def route_after_research(
    state: AgentState,
) -> Literal[
    "research_tools",
    "kb_search_agent",
    "satisfaction_handler",
    "__end__",
]:
    """
    research_agent only owns the solved-ticket + satisfaction layer:
      - phase == awaiting_satisfaction + last msg is AI
                                                → END (just asked the question,
                                                  wait for user reply)
      - phase == awaiting_satisfaction + last msg is human
                                                → satisfaction_handler (user replied)
      - phase in (awaiting_self_or_human,
                  awaiting_category_confirmation) → forward to kb_search_agent
      - tool_calls present                      → execute the tool
      - phase == no_solved_match                → fall through to KB search
      - otherwise                               → END
    """
    research_phase = state.get("helpdesk_research_phase", "")

    if research_phase == "awaiting_satisfaction":
        last_msg = state["messages"][-1]
        if getattr(last_msg, "type", "") == "ai":
            # research_agent just produced the answer — wait for user's reply
            print("[router] solved match found → ending (awaiting user satisfaction)")
            return "__end__"
        # User has replied to the satisfaction question
        print("[router] user replied to satisfaction check → satisfaction_handler")
        return "satisfaction_handler"

    ticket_phase = state.get("helpdesk_ticket_phase", "")
    if ticket_phase in (
        "awaiting_self_or_human",
        "awaiting_category_confirmation",
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


# [NODE] Fires only when research_phase == "awaiting_satisfaction" and the
# user has just replied to "did that answer your question?". Pure keyword
# matching — no LLM call for the decision itself, only for the closing reply.
async def satisfaction_handler(state: AgentState) -> dict:
    """Handle the user's satisfaction response using keyword matching.

    Avoids an internal LLM classification call (which would stream labels like
    NOT_SATISFIED to the user). The closing message for the satisfied path still
    uses an LLM so it streams naturally.
    """
    user_message = _latest_user_message(state)
    user_id = state.get("user_id", "anonymous")
    user_lower = user_message.lower().strip()
    print(f"[satisfaction_handler] user_id={user_id!r} response={user_message!r}")

    satisfied_signals = {
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
        "more",
        "again",
        "another",
    }

    words = set(re.sub(r"[^\w\s]", "", user_lower).split())
    is_not_satisfied = bool(words & not_satisfied_signals)
    is_satisfied = bool(words & satisfied_signals) and not is_not_satisfied

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


# [ROUTER] Reads satisfaction_handler's verdict: not satisfied → KB search,
# satisfied → end (satisfaction_handler already sent the closing message).
def route_after_satisfaction(
    state: AgentState,
) -> Literal["kb_search_agent", "__end__"]:
    """After satisfaction check: not satisfied → KB search, satisfied → END."""
    phase = state.get("helpdesk_research_phase", "")
    if phase == "not_satisfied":
        print("[router] not satisfied → KB search")
        return "kb_search_agent"
    print("[router] satisfied → ending")
    return "__end__"
