"""
Graph entry point: classify_message labels the message as "greeting" /
"research" / "ticket_related", and route_by_message_type sends it to one
of the three top-level branches.
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


# Graph entry point. Skips the LLM call while a mid-flow phase is active
# (a short reply like "keep it" could get mislabeled) — only a bare
# greeting breaks out of the flow and resets state.
async def classify_message(state: AgentState) -> dict:
    """Classify the user message and store the result in state."""
    user_message = _latest_user_message(state)

    research_phase = state.get("helpdesk_research_phase", "")
    ticket_phase = state.get("helpdesk_ticket_phase", "")
    mid_flow = research_phase == "awaiting_satisfaction" or ticket_phase in (
        "awaiting_self_or_human",
        "awaiting_category_confirmation",
        "awaiting_final_confirmation",
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


# Reads state["message_type"] and sends the turn to one of 3 branches.
# Everything downstream (KB search, satisfaction check, ticket creation)
# happens inside research_agent's branch, not as direct edges from here.
def route_by_message_type(
    state: AgentState,
) -> Literal[
    "greeting_agent",
    "research_agent",
    "ticket_status_agent",
]:
    message_type = state.get("message_type", "research")
    routing_map = {
        "greeting": "greeting_agent",
        "research": "research_agent",
        "ticket_related": "ticket_status_agent",
    }
    next_node = routing_map.get(message_type, "research_agent")
    print(f"[router] message_type={message_type!r} → routing to {next_node!r}")
    return next_node
