"""
Third top-level branch (from classifier.route_by_message_type, message_type
== "ticket_related") — answers "what's the status of my ticket" style
questions by calling get_user_tickets. Independent of the research/ticket
creation flow (research.py / kb_search.py / .../ticket_draft.py); never
sets any helpdesk_* phase field.
"""

from typing import Literal

from domain.state import AgentState
from domain.helpdesk.prompts import ticket_status_agent_system_prompt
from domain.helpdesk.tools.helpdesk_tools import ticket_llm


# [NODE] Third top-level branch (from route_by_message_type, message_type
# == "ticket_related") — answers "what's the status of my ticket" style
# questions by calling get_user_tickets. Independent of the research/ticket
# creation flow above; never sets any helpdesk_* phase field.
async def ticket_status_agent(state: AgentState) -> dict:
    """
    Answers questions about the user's existing tickets using tool-calling.

    Flow (two turns through this node):
      Turn 1 — LLM sees the user question + tool schema
               → emits tool_calls for get_user_tickets
               → graph routes to ticket_status_tools (ToolNode)
      Turn 2 — ToolNode has appended a ToolMessage with DB results
               → LLM reads it and produces a final plain-text reply
               → graph routes to END
    """
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


# [ROUTER] Standard tool-calling loop router: tool_calls present → run
# ticket_status_tools; plain-text reply → end.
def should_continue_ticket_status_agent(
    state: AgentState,
) -> Literal["ticket_status_tools", "__end__"]:
    """
    After ticket_status_agent runs:
      - LLM emitted tool_calls  → go to ticket_status_tools (ToolNode executes the tool)
      - LLM gave a plain reply  → go to END
    """
    last_message = state["messages"][-1]
    if getattr(last_message, "tool_calls", None):
        print("[ticket_status_agent] tool call detected → running ticket_status_tools")
        return "ticket_status_tools"
    print("[ticket_status_agent] no tool call → ending")
    return "__end__"
