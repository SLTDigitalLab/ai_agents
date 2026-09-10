"""
Third top-level branch (message_type == "ticket_related") — answers
"what's the status of my ticket" style questions via get_user_tickets.
Independent of the research/ticket-creation flow; never sets any
helpdesk_* phase field.
"""

from typing import Literal

from domain.state import AgentState
from domain.helpdesk.prompts import ticket_status_agent_system_prompt
from domain.helpdesk.tools.helpdesk_tools import ticket_llm


# Turn 1 — LLM emits a tool call for get_user_tickets -> ticket_status_tools.
# Turn 2 — LLM reads the DB result and gives a final reply -> END.
async def ticket_status_agent(state: AgentState) -> dict:
    """Answers questions about the user's existing tickets using tool-calling."""
    user_id = state.get("user_id", "anonymous")
    messages = state.get("messages", [])

    print(f"[ticket_status_agent] user_id={user_id!r} | messages in state={len(messages)}")

    system_prompt = {
        "role": "system",
        "content": ticket_status_agent_system_prompt(user_id),
    }

    response = await ticket_llm.ainvoke([system_prompt, *messages])
    print(f"[ticket_status_agent] tool_calls={getattr(response, 'tool_calls', [])}")
    return {"messages": [response]}


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
