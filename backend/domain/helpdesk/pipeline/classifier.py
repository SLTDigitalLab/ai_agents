"""
Graph entry point: classify_message labels the message as "greeting" /
"research" / "ticket_related", and route_by_message_type sends it to one
of the three top-level branches.

On a fresh greeting, classify_message's own LLM call also produces the
reply text (see ClassificationResult / classifier_prompts.py) — this
piggybacks the greeting reply onto the classification call instead of
paying for a second sequential round-trip in greeting_agent. When that
happens, route_by_message_type sends the turn straight to END;
greeting_agent is still used as the fallback for the mid-flow
greeting-reset case below, where no reply was generated here.
"""

from typing import Literal, Optional

from langchain_core.messages import AIMessage
from pydantic import BaseModel, Field

from domain.state import AgentState
from domain.helpdesk.pipeline.helpers import (
    llm,
    _latest_user_message,
    _is_standalone_greeting,
    _normalize_message_type,
)
from domain.helpdesk.prompts import CLASSIFIER_SYSTEM_PROMPT


class ClassificationResult(BaseModel):
    """Structured output for classify_message's combined classify+reply call."""

    message_type: Literal["greeting", "research", "ticket_related", "unclear"] = Field(
        description="Routing label for the message."
    )
    greeting_reply: Optional[str] = Field(
        default=None,
        description=(
            "The reply shown to the user, populated only when message_type "
            "is 'greeting'; null/empty for every other message_type."
        ),
    )


_classifier_llm = llm.with_structured_output(ClassificationResult)


# Graph entry point. Skips the LLM call while a mid-flow phase is active
# (a short reply like "keep it" could get mislabeled) — only a bare
# greeting breaks out of the flow and resets state.
async def classify_message(state: AgentState) -> dict:
    """Classify the user message and store the result in state."""
    user_message = _latest_user_message(state)

    research_phase = state.get("helpdesk_research_phase", "")
    ticket_phase = state.get("helpdesk_ticket_phase", "")
    ticket_status_phase = state.get("helpdesk_ticket_status_phase", "")
    mid_flow = research_phase == "awaiting_satisfaction" or ticket_phase in (
        "awaiting_self_or_human",
        "awaiting_category_confirmation",
        "awaiting_final_confirmation",
        "awaiting_retry_clarification",
        "awaiting_category_clarification",
    )
    ticket_status_mid_flow = ticket_status_phase == "awaiting_incident_id"

    if mid_flow or ticket_status_mid_flow:
        if _is_standalone_greeting(user_message):
            # User greeted while a flow was in progress — reset flow and greet back
            print(
                f"[classifier] greeting during mid-flow "
                f"(research_phase={research_phase!r}, ticket_phase={ticket_phase!r}, "
                f"ticket_status_phase={ticket_status_phase!r}) "
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
                "helpdesk_ticket_status_phase": "",
            }
        if ticket_status_mid_flow:
            # Waiting on an incident ID — the user's reply (e.g. a bare
            # "1-1HRRK5X") would otherwise get reclassified from scratch
            # with no memory that ticket_status_agent just asked for it.
            print(
                f"[classifier] awaiting incident ID (ticket_status_phase={ticket_status_phase!r}) "
                "→ routing to ticket_status_agent"
            )
            return {"message_type": "ticket_related"}
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

    try:
        result = await _classifier_llm.ainvoke(messages)
        message_type = _normalize_message_type(result.message_type)
        greeting_reply = (result.greeting_reply or "").strip()
    except Exception as exc:
        # Never let a malformed structured-output response break routing —
        # fail safe to "research" with no precomputed reply, same as the
        # old plain-text parser's default for an unrecognized label.
        print(f"[classifier] WARNING: classification call failed ({exc}) → defaulting to 'research'")
        message_type, greeting_reply = "research", ""

    print(f"[classifier] message_type={message_type!r} greeting_reply={greeting_reply!r}")

    if message_type == "greeting" and greeting_reply:
        # Reply already generated in this same call — end the turn here
        # instead of paying for a second LLM round-trip in greeting_agent.
        return {
            "message_type": message_type,
            "messages": [AIMessage(content=greeting_reply)],
        }

    return {"message_type": message_type}


# Reads state["message_type"] and sends the turn to one of 3 branches, or
# straight to END when classify_message already generated a greeting
# reply in the same call (see classify_message's ClassificationResult
# short-circuit above). Everything downstream (KB search, satisfaction
# check, ticket creation) happens inside research_agent's branch, not as
# direct edges from here.
def route_by_message_type(
    state: AgentState,
) -> Literal[
    "greeting_agent",
    "research_agent",
    "ticket_status_agent",
    "__end__",
]:
    message_type = state.get("message_type", "research")

    # classify_message only appends a message when it already answered a
    # fresh greeting in-call — a mid-flow greeting-reset (see
    # classify_message's mid_flow branch) sets message_type="greeting"
    # without appending one, and still needs greeting_agent to reply.
    last_message = state["messages"][-1] if state.get("messages") else None
    already_answered = (
        message_type == "greeting" and getattr(last_message, "type", "") == "ai"
    )
    if already_answered:
        print("[router] greeting already answered by classify_message → ending")
        return "__end__"

    routing_map = {
        "greeting": "greeting_agent",
        "research": "research_agent",
        "ticket_related": "ticket_status_agent",
    }
    next_node = routing_map.get(message_type, "research_agent")
    print(f"[router] message_type={message_type!r} → routing to {next_node!r}")
    return next_node
