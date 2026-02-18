"""
Shared agent state definition for the LangGraph multi-agent system.

All agent graphs share this single state schema. Fields are accumulated
(messages) or overwritten (everything else) on each graph step.
"""

from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    """Central state passed through every node in every agent graph."""

    # LangGraph message history – uses the built-in reducer so new
    # messages are *appended* rather than replacing the list.
    messages: Annotated[list[BaseMessage], add_messages]

    # Which specialist agent is active (e.g. "hr", "finance").
    # Used to select the correct Qdrant collection at search time.
    agent_id: str

    # Authenticated caller – passed through for API / DB lookups.
    user_id: str

    # Slot-filling state for Archetype 3 (KB + Form) agents.
    form_slots: dict

    # Set by the Supervisor node to tell the router where to go next.
    next_node: str
