"""
Helpdesk agent (agent_id="helpdesk_dev") — LangGraph StateGraph that answers
IT/helpdesk questions and creates support tickets when it can't.

The actual node/router logic lives in domain/helpdesk/pipeline/ (one file
per stage), prompts in domain/helpdesk/prompts/, tools in
domain/helpdesk/tools/. This file just wires everything into the graph.

Flow:
  classify_message -> greeting / research / ticket_related
                    -> (fresh greeting) already replied in-call -> END

  greeting_agent      -> one reply -> END
                         (fallback only: mid-flow greeting-reset case,
                         where classify_message didn't generate a reply)

  research_agent      -> search solved tickets -> if matched, ask if it
                         helped -> else kb_search_agent -> search KB ->
                         validate_kb_answer -> present answer / clarify /
                         create ticket -> self_or_human_handler ->
                         check_duplicates -> draft_ticket ->
                         confirm_category_handler -> save_ticket

  ticket_status_agent -> loops over get_user_tickets for status questions

Multi-turn state is tracked via helpdesk_research_phase /
helpdesk_ticket_phase (domain/state.py) so a node knows if it's mid-flow.
"""

from langgraph.graph import START, END, StateGraph
from langgraph.prebuilt import ToolNode

from domain.state import AgentState
from domain.helpdesk.pipeline.helpers import llm, INTERNAL_LLM_TAG
from domain.helpdesk.tools.helpdesk_tools import TICKET_TOOLS, SOLVED_TICKET_TOOLS, KB_TOOLS

from domain.helpdesk.pipeline.classifier import classify_message, route_by_message_type
from domain.helpdesk.pipeline.greeting import greeting_agent
from domain.helpdesk.pipeline.research import (
    research_agent,
    route_after_research,
    satisfaction_handler,
    route_after_satisfaction,
)
from domain.helpdesk.pipeline.kb_search import (
    kb_search_agent,
    should_continue_kb_search,
    validate_kb_answer,
    route_after_kb_validation,
)
from domain.helpdesk.pipeline.self_or_human import self_or_human_handler, route_after_self_or_human
from domain.helpdesk.pipeline.duplicates import check_duplicates, route_after_duplicate_check
from domain.helpdesk.pipeline.ticket_draft import (
    draft_ticket,
    category_clarification_handler,
    confirm_category_handler,
    route_after_category_confirmation,
    save_ticket,
)
from domain.helpdesk.pipeline.ticket_status_agent import ticket_status_agent, should_continue_ticket_status_agent

# Re-exported for the eval scripts (run_accuracy_eval.py etc.) that import
# the category classifier variants straight from this file.
from domain.helpdesk.pipeline.category_classification import (  # noqa: F401
    classify_ticket_category,
    classify_ticket_category_vector,
    classify_ticket_category_finetuned,
    classify_ticket_category_finetuned_vector,
    classify_ticket_category_examples,
    classify_ticket_category_pipeline,
    _parse_category_draft,
    _resolve_category,
    _load_valid_categories,
)


# Builds the LangGraph StateGraph. Called fresh per HTTP request by
# domain/registry.py, compiled with a per-request checkpointer in
# routers/chat.py.
def build_helpdesk_workflow() -> StateGraph:
    workflow = StateGraph(AgentState)

    # ── Nodes ────────────────────────────────────────────────────────────
    # Entry point: decides greeting / research / ticket_related.
    workflow.add_node("classify_message", classify_message)

    # Small talk — one reply, then ends.
    workflow.add_node("greeting_agent", greeting_agent)

    # Searches previously-solved tickets for a matching answer.
    workflow.add_node("research_agent", research_agent)
    workflow.add_node("research_tools", ToolNode(SOLVED_TICKET_TOOLS))

    # Asks "did that answer help?" after a solved-ticket match.
    workflow.add_node("satisfaction_handler", satisfaction_handler)

    # Searches the knowledge base when no solved ticket matched.
    workflow.add_node("kb_search_agent", kb_search_agent)
    workflow.add_node("kb_search_tools", ToolNode(KB_TOOLS))

    # Decides if the KB answer is good enough, needs clarifying, or means
    # "make a ticket".
    workflow.add_node("validate_kb_answer", validate_kb_answer)

    # Asks "fixed it yourself, or want a ticket raised?".
    workflow.add_node("self_or_human_handler", self_or_human_handler)

    # Ticket creation: checks for an existing open ticket first.
    workflow.add_node("check_duplicates", check_duplicates)

    # Classifies the category and shows a draft ticket to review.
    workflow.add_node("draft_ticket", draft_ticket)

    # Low-confidence category → one follow-up question, then drafts.
    workflow.add_node("category_clarification_handler", category_clarification_handler)

    # User keeps or changes the suggested category.
    workflow.add_node("confirm_category_handler", confirm_category_handler)

    # Writes the ticket to the database.
    workflow.add_node("save_ticket", save_ticket)

    # Ticket status lookups — separate from draft/save above, read-only.
    workflow.add_node("ticket_status_agent", ticket_status_agent)
    workflow.add_node("ticket_status_tools", ToolNode(TICKET_TOOLS))

    # ── Edges ────────────────────────────────────────────────────────────
    workflow.add_edge(START, "classify_message")

    workflow.add_conditional_edges(
        "classify_message",
        route_by_message_type,
        {
            "greeting_agent": "greeting_agent",
            "research_agent": "research_agent",
            "ticket_status_agent": "ticket_status_agent",
            # A fresh greeting whose reply classify_message already
            # generated in the same call (see classifier.py) — no need to
            # pay for a second LLM round-trip in greeting_agent.
            "__end__": END,
        },
    )

    workflow.add_edge("greeting_agent", END)

    workflow.add_conditional_edges(
        "research_agent",
        route_after_research,
        {
            "research_tools": "research_tools",
            "kb_search_agent": "kb_search_agent",
            "satisfaction_handler": "satisfaction_handler",
            "__end__": END,
        },
    )
    workflow.add_edge("research_tools", "research_agent")

    workflow.add_conditional_edges(
        "satisfaction_handler",
        route_after_satisfaction,
        {
            "kb_search_agent": "kb_search_agent",
            "__end__": END,
        },
    )

    workflow.add_conditional_edges(
        "kb_search_agent",
        should_continue_kb_search,
        {
            "kb_search_tools": "kb_search_tools",
            "validate_kb_answer": "validate_kb_answer",
            "self_or_human_handler": "self_or_human_handler",
            "confirm_category_handler": "confirm_category_handler",
            "category_clarification_handler": "category_clarification_handler",
            "check_duplicates": "check_duplicates",
            "__end__": END,
        },
    )
    workflow.add_edge("kb_search_tools", "kb_search_agent")

    # not_in_kb -> check_duplicates, everything else -> END (wait for reply)
    workflow.add_conditional_edges(
        "validate_kb_answer",
        route_after_kb_validation,
        {
            "check_duplicates": "check_duplicates",
            "__end__": END,
        },
    )

    # self -> END, ticket -> check_duplicates, follow-up -> kb_search_agent
    workflow.add_conditional_edges(
        "self_or_human_handler",
        route_after_self_or_human,
        {
            "check_duplicates": "check_duplicates",
            "kb_search_agent": "kb_search_agent",
            "__end__": END,
        },
    )

    # duplicate found -> END, else -> draft_ticket
    workflow.add_conditional_edges(
        "check_duplicates",
        route_after_duplicate_check,
        {
            "draft_ticket": "draft_ticket",
            "__end__": END,
        },
    )

    # Both end waiting for the user's confirmation reply.
    workflow.add_edge("draft_ticket", END)
    workflow.add_edge("category_clarification_handler", END)

    # confirmed -> save_ticket, still unclear -> END
    workflow.add_conditional_edges(
        "confirm_category_handler",
        route_after_category_confirmation,
        {
            "save_ticket": "save_ticket",
            "__end__": END,
        },
    )

    workflow.add_edge("save_ticket", END)

    workflow.add_conditional_edges(
        "ticket_status_agent",
        should_continue_ticket_status_agent,
        {
            "ticket_status_tools": "ticket_status_tools",
            "__end__": END,
        },
    )
    workflow.add_edge("ticket_status_tools", "ticket_status_agent")

    return workflow
