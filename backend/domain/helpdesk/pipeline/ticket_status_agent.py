"""
Third top-level branch (message_type == "ticket_related") — answers
"what's the status of my ticket" style questions via check_incident_status,
a real call to SLT's incident CRM (see domain/helpdesk/tools/
incident_status_tools.py), looked up by incident ID rather than by
user_id. Independent of the research/ticket-creation flow; only touches
helpdesk_ticket_status_phase (see domain/state.py), not the research/
ticket-creation phase fields.
"""

from typing import Literal

from domain.state import AgentState
from domain.helpdesk.prompts import ticket_status_agent_system_prompt
from domain.helpdesk.tools.helpdesk_tools import ticket_llm


# Turn 1 — LLM either asks for the incident ID (plain text, no tool call —
# see ticket_status_agent_system_prompt's STEP 1) or, if the user already
# gave one, emits a tool call for check_incident_status -> ticket_status_tools.
# Turn 2 (only when a tool ran) — LLM reads the result and gives a final
# reply -> END.
async def ticket_status_agent(state: AgentState) -> dict:
    """Answers questions about an incident's status using tool-calling."""
    user_id = state.get("user_id", "anonymous")
    messages = state.get("messages", [])

    print(f"[ticket_status_agent] user_id={user_id!r} | messages in state={len(messages)}")

    system_prompt = {
        "role": "system",
        "content": ticket_status_agent_system_prompt(user_id),
    }

    # A ToolMessage as the last message means check_incident_status already
    # ran this turn — this is Turn 2 (final answer), not a fresh ask.
    last_message = messages[-1] if messages else None
    tool_already_ran = getattr(last_message, "type", "") == "tool"

    response = await ticket_llm.ainvoke([system_prompt, *messages])
    print(f"[ticket_status_agent] tool_calls={getattr(response, 'tool_calls', [])}")

    if getattr(response, "tool_calls", None) or tool_already_ran:
        # Either looking the ID up right now, or done after Turn 2's final
        # answer — either way, no longer waiting on the user for an ID.
        return {"messages": [response], "helpdesk_ticket_status_phase": ""}

    # Plain-text reply with nothing looked up yet this turn — see
    # ticket_status_agent_system_prompt's STEP 1: this is the "please give
    # me your incident ID" ask, so classify_message needs to route the
    # user's next (likely bare-ID) reply straight back here.
    return {"messages": [response], "helpdesk_ticket_status_phase": "awaiting_incident_id"}


def should_continue_ticket_status_agent(
    state: AgentState,
) -> Literal["ticket_status_tools", "__end__"]:
    """tool_calls present → run ticket_status_tools; plain reply → end."""
    last_message = state["messages"][-1]
    if getattr(last_message, "tool_calls", None):
        print("[ticket_status_agent] tool call detected → running ticket_status_tools")
        return "ticket_status_tools"
    print("[ticket_status_agent] no tool call → ending")
    return "__end__"
