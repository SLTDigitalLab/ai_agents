"""
STEP 1 — CLASSIFIER NODE, STEP 2 — ROUTER FUNCTION.

Graph entry point: classify_message labels the message as one of
"greeting" / "research" / "ticket_related", and route_by_message_type sends
it to exactly one of the three top-level branches. See
domain/helpdesk/pipeline/graph.py's module docstring for the overall flow.
"""

from typing import Literal

from domain.state import AgentState
from domain.helpdesk.pipeline.helpers import (
    llm,
    _message_to_text,
    _latest_user_message,
    _is_standalone_greeting,
    _normalize_message_type,
)
from domain.helpdesk.prompts import CLASSIFIER_SYSTEM_PROMPT


# [NODE] Graph entry point (wired to START). Sets state["message_type"] to
# one of "greeting" / "research" / "ticket_related", which route_by_message_type
# reads next. Makes one LLM call on a fresh turn; makes NO LLM call while a
# multi-turn phase is active (see the mid_flow branch below).
async def classify_message(state: AgentState) -> dict:
    """Classify the user message and store the result in state.

    If a mid-flow phase is active, skip the LLM classifier entirely — it has
    no visibility into the pending question, so a short flow reply (e.g.
    "keep it") could get mislabeled as a greeting. Use a deterministic
    bare-greeting check instead: only a message that IS just a greeting
    (e.g. "Good Morning") breaks out of the active phase and resets state so
    the user gets a proper welcome response; anything else keeps the flow.
    """
    user_message = _latest_user_message(state)

    research_phase = state.get("helpdesk_research_phase", "")
    ticket_phase = state.get("helpdesk_ticket_phase", "")
    mid_flow = research_phase == "awaiting_satisfaction" or ticket_phase in (
        "awaiting_self_or_human",
        "awaiting_category_confirmation",
        "awaiting_retry_clarification",
        "awaiting_category_clarification",
    )

    if mid_flow:
        if _is_standalone_greeting(user_message):
            # User greeted while a flow was in progress — reset flow and greet back
            print(
                f"[classifier] greeting during mid-flow "
                f"(research_phase={research_phase!r}, ticket_phase={ticket_phase!r}) "
                "→ resetting flow, routing to greeting_agent"
            )
            return {
                "message_type": "greeting",
                "helpdesk_research_phase": "",
                "helpdesk_ticket_phase": "",
                "helpdesk_original_query": "",
                "helpdesk_draft_ticket_id": "",
                "helpdesk_draft_main_category": "",
                "helpdesk_draft_sub_category": "",
                "helpdesk_draft_continuation": False,
                "helpdesk_retry_count": 0,
                "helpdesk_category_clarify_count": 0,
            }
        # Non-greeting mid-flow message → keep the active phase
        print(
            f"[classifier] mid-flow phase active (research_phase={research_phase!r}, "
            f"ticket_phase={ticket_phase!r}) → routing to research_agent"
        )
        return {"message_type": "research"}

    messages = [
        {"role": "system", "content": CLASSIFIER_SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]

    response = await llm.ainvoke(messages)
    raw = _message_to_text(response)
    message_type = _normalize_message_type(raw)
    print(f"[classifier] raw={raw!r} → message_type={message_type!r}")

    return {"message_type": message_type}


# [ROUTER] Reads state["message_type"] (set by classify_message) and sends
# the turn to exactly one of the 3 top-level branches below.
def route_by_message_type(
    state: AgentState,
) -> Literal[
    "greeting_agent",
    "research_agent",
    "ticket_status_agent",
]:
    """Top-level classifier routing — exactly three destinations.

    All follow-up steps (solved-ticket check, KB search, satisfaction check,
    self-or-human choice, category confirmation) happen downstream of
    research_agent, not as direct branches from here.
    """
    message_type = state.get("message_type", "research")
    routing_map = {
        "greeting": "greeting_agent",
        "research": "research_agent",
        "ticket_related": "ticket_status_agent",
    }
    next_node = routing_map.get(message_type, "research_agent")
    print(f"[router] message_type={message_type!r} → routing to {next_node!r}")
    return next_node
