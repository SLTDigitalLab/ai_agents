"""
Shared agent state definition for the LangGraph multi-agent system.

All agent graphs share this single state schema. Fields are accumulated
(messages) or overwritten (everything else) on each graph step.
"""

from typing import Annotated, TypedDict, Literal, NotRequired

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
    user_name: NotRequired[str]

    # Current chat thread id. Used to collect retrieved evidence per request.
    thread_id: NotRequired[str]

    # Slot-filling state for Archetype 3 (KB + Form) agents.
    form_slots: dict

    # Set by the Supervisor node to tell the router where to go next.
    next_node: str

    # Detected sentiment from input guardrails (e.g. "frustrated", "neutral").
    # Used by agent nodes to adapt response tone.
    sentiment: str

    # Helpdesk classifier output used by the greeting/research/ticket router.
    message_type: NotRequired[str]

    # Helpdesk n8n resume metadata persisted across turns so a paused workflow
    # can continue instead of starting from the webhook again.
    # Managed internally by helpdesk_n8n_agent archetype.
    helpdesk_execution_id: NotRequired[str]
    helpdesk_resume_url: NotRequired[str]
    helpdesk_waiting_for_input: NotRequired[bool]

    # Research workflow phase tracking for the helpdesk solved-ticket → KB flow.
    helpdesk_research_phase: NotRequired[str]
    helpdesk_original_query: NotRequired[str]

    # Ticket creation flow tracking for the helpdesk agent.
    helpdesk_ticket_phase: NotRequired[str]
    helpdesk_draft_ticket_id: NotRequired[str]
    helpdesk_draft_main_category: NotRequired[str]
    helpdesk_draft_sub_category: NotRequired[str]
    helpdesk_retry_count: NotRequired[int]
    # Confidence-gated clarification (see draft_ticket() / category_
    # clarification_handler() in domain/helpdesk/pipeline/ticket_draft.py):
    # 0 until draft_ticket
    # asks a low-confidence clarifying question, then 1 — caps the loop at
    # one re-ask so a structurally ambiguous ticket (see
    # helpdesk-category-accuracy-gap project memory) can't loop forever on
    # a question the user can't actually answer.
    helpdesk_category_clarify_count: NotRequired[int]
    # True if a different node already sent a user-visible reply earlier in
    # the same turn as this ticket draft, decided once on the first call into
    # draft_ticket() and reused across its Turn 1 -> Turn 2 tool-call cycle.
    helpdesk_draft_continuation: NotRequired[bool]

    # Supervisor-only routing fields
    routing_action: NotRequired[str]
    routing_reason: NotRequired[str]
    routed_agent_id: NotRequired[str]
    routing_scores: NotRequired[dict[str, float]]
    last_specialist_agent: NotRequired[str]
    pending_clarification: NotRequired[bool]
    clarification_options: NotRequired[list[str]]
    original_query: NotRequired[str]
    delegation_query: NotRequired[str]

    # Multi-specialist fan-out (set when routing is ambiguous between two specialists).
    routed_agent_ids: NotRequired[list[str]]
    specialist_answers: NotRequired[dict[str, str]]
    # Per-specialist focused sub-queries produced by decompose_query. Each routed
    # specialist receives only its own sub-question instead of the full compound query.
    specialist_queries: NotRequired[dict[str, str]]

    # True when a specialist graph is invoked indirectly by the supervisor
    # (Workmate AI). Specialists use this to keep the unified "Workmate AI"
    # voice and never reveal that multiple agents exist.
    via_supervisor: NotRequired[bool]