"""
Helpdesk agent (agent_id="helpdesk_dev") — a LangGraph StateGraph that answers
IT/helpdesk questions and creates support tickets when it can't.

This file owns build_helpdesk_workflow(), same as every other domain/
archetypes/*.py file owns its own build_X_workflow(). The actual pipeline
logic — one file per stage — lives under domain/helpdesk/pipeline/; this
file just imports each stage's [NODE]/[ROUTER] functions and wires them
into the graph. domain/helpdesk/ also holds prompts/ and tools/ for the
same stages.

READ THIS FIRST IF YOU'RE NEW TO THE HELPDESK AGENT
-------------------------------------------------------------------------
  domain/helpdesk/pipeline/
    helpers.py                 shared `llm` client, INTERNAL_LLM_TAG, and the
                                small pure-Python helpers every other file uses
    classifier.py               [NODE] classify_message, [ROUTER] route_by_message_type
    greeting.py                  [NODE] greeting_agent
    research.py                  [NODE] research_agent, satisfaction_handler
                                  + their routers
    kb_search.py                  [NODE] kb_search_agent, validate_kb_answer
                                  + their routers
    self_or_human.py              [NODE] self_or_human_handler + router
    duplicates.py                  [NODE] check_duplicates + router
    category_classification.py    the category PREDICTION PIPELINE — every
                                   classify_ticket_category*() variant
    ticket_draft.py                [NODE] draft_ticket, category_clarification_
                                   handler, confirm_category_handler,
                                   save_ticket + routers
    ticket_status_agent.py           [NODE] ticket_status_agent + router
  domain/helpdesk/prompts/        system prompts, split to match the stages above
  domain/helpdesk/tools/          @tool definitions + retrieval helpers

Every [NODE]/[ROUTER] function is tagged the same way it always was:

  [NODE]    A graph node (registered with workflow.add_node in
            build_helpdesk_workflow below). Nodes read AgentState,
            optionally call an LLM/tool/DB, and return a partial state dict
            that LangGraph merges back in.
  [ROUTER]  A conditional-edge function (registered with
            workflow.add_conditional_edges). Routers make NO LLM calls and
            emit NO messages — they only look at state and return the name
            of the next node to run.
  [HELPER]  A small pure-Python utility used by nodes/routers in the same
            (or an upstream) file.
  [EVAL-ONLY] Standalone classifier variant used by
            domain/helpdesk/scripts/run_accuracy_eval.py for A/B comparison. Not
            part of the live conversational graph.

HIGH-LEVEL FLOW
-------------------------------------------------------------------------
Every turn enters at classify_message, which labels the message as one of
"greeting" / "research" / "ticket_related" and route_by_message_type sends
it to exactly one of three branches:

  greeting_agent   → single LLM reply → END

  research_agent   → search solved tickets → (match) ask if it helped
                    → (no match) kb_search_agent → search the knowledge
                      base → validate_kb_answer decides: present the
                      answer / ask to clarify / give up and create a
                      ticket → self_or_human_handler → check_duplicates
                    → draft_ticket → confirm_category_handler
                    → save_ticket

  ticket_status_agent → tool-calling loop over get_user_tickets for
                      "what's the status of my ticket" style questions

Multi-turn progress through the research/ticket branch is tracked in
AgentState via the helpdesk_research_phase / helpdesk_ticket_phase fields
(see domain/state.py) and persisted per-conversation by the Postgres
checkpointer, so a node can tell on the next HTTP request whether it's
mid-flow (e.g. waiting for a yes/no answer) or starting fresh.
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

# [EVAL-ONLY] Re-exported so run_accuracy_eval.py / run_pipeline_eval.py /
# run_prediction_detail.py can keep importing the category classifier
# variants (and their shared helpers) straight from this file, same as
# before the pipeline logic was split out.
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


# [GRAPH BUILDER] Wires every [NODE]/[ROUTER] function above into the
# actual LangGraph StateGraph. Called fresh per HTTP request by
# domain/registry.py, then compiled with a per-request Postgres
# checkpointer in routers/chat.py — see this file's module docstring for
# the overall flow diagram.
def build_helpdesk_workflow() -> StateGraph:
    workflow = StateGraph(AgentState)

    # ── Nodes ─────────────────────────────────────────────────────────────
    # Registers each [NODE] function under the name used in add_edge /
    # add_conditional_edges below. ToolNode(...) wraps a list of @tool
    # functions so LangGraph can execute whichever one the preceding node's
    # LLM call chose via tool_calls.
    #
    # Plain-English, what-each-node-actually-does cheat sheet:

    # Entry point. Reads the user's message and DECIDES what kind of thing
    # it is: "greeting" / "research" (a question) / "ticket_related".
    # Doesn't answer anything itself — just picks where to send it next.
    workflow.add_node("classify_message", classify_message)

    # Branch 1 — small talk. Opposite job of classify_message: instead of
    # deciding, it DOES the greeting — one LLM reply ("hi/hello/thanks"
    # style), then the conversation ends.
    workflow.add_node("greeting_agent", greeting_agent)

    # Branch 2 — research (question-answering), step 1. Searches
    # previously-solved tickets for a matching answer.
    workflow.add_node("research_agent", research_agent)

    # Tool-runner that executes the "search solved tickets" call
    # research_agent asked for, then hands control back to research_agent.
    workflow.add_node("research_tools", ToolNode(SOLVED_TICKET_TOOLS))

    # If a solved-ticket match was found, asks "did that answer help?"
    # and reads the user's yes/no reply.
    workflow.add_node("satisfaction_handler", satisfaction_handler)

    # If there's no solved-ticket match (or the user said "no" above),
    # searches the knowledge base instead.
    workflow.add_node("kb_search_agent", kb_search_agent)

    # Tool-runner that executes the "search knowledge base" call, then
    # hands control back to kb_search_agent.
    workflow.add_node("kb_search_tools", ToolNode(KB_TOOLS))

    # Checks the KB answer's quality: good enough to show, too vague (ask
    # user to clarify), or nothing relevant found (go make a ticket).
    workflow.add_node("validate_kb_answer", validate_kb_answer)

    # After an answer is shown, asks "did you fix it yourself, or do you
    # want a human / a ticket raised?"
    workflow.add_node("self_or_human_handler", self_or_human_handler)

    # Branch 2b — ticket creation (reached when nothing solved the issue).
    # Checks if the user already has an open ticket for the same problem,
    # to avoid creating a duplicate.
    workflow.add_node("check_duplicates", check_duplicates)

    # Classifies the ticket's category and shows the user a draft ticket
    # to review.
    workflow.add_node("draft_ticket", draft_ticket)

    # If the category classifier wasn't confident, asks one follow-up
    # question — always ends in a ticket draft, never a second question.
    workflow.add_node("category_clarification_handler", category_clarification_handler)

    # Lets the user keep or change the suggested category.
    workflow.add_node("confirm_category_handler", confirm_category_handler)

    # Writes the final ticket to the database. End of the ticket path.
    workflow.add_node("save_ticket", save_ticket)

    # Branch 3 — ticket status questions. Handles "what's the status of my
    # ticket" style questions, using tools in a loop. (Not to be confused
    # with draft_ticket/save_ticket above — this branch never creates or
    # modifies a ticket, only looks up existing ones.)
    workflow.add_node("ticket_status_agent", ticket_status_agent)

    # Tool-runner that executes get_user_tickets for ticket_status_agent,
    # then hands control back to ticket_status_agent.
    workflow.add_node("ticket_status_tools", ToolNode(TICKET_TOOLS))

    # ── Entry point ────────────────────────────────────────────────────────
    workflow.add_edge(START, "classify_message")

    # ── Classifier → agents (exactly 3 destinations) ──────────────────────
    workflow.add_conditional_edges(
        "classify_message",
        route_by_message_type,
        {
            "greeting_agent": "greeting_agent",
            "research_agent": "research_agent",
            "ticket_status_agent": "ticket_status_agent",
        },
    )

    # ── Simple agents → END ───────────────────────────────────────────────
    workflow.add_edge("greeting_agent", END)

    # ── research_agent: fresh search, satisfaction, or hand off to kb layer ─
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

    # ── Satisfaction flow ────────────────────────────────────────────────
    workflow.add_conditional_edges(
        "satisfaction_handler",
        route_after_satisfaction,
        {
            "kb_search_agent": "kb_search_agent",
            "__end__": END,
        },
    )

    # ── kb_search_agent: fresh search, or dispatch a mid-flow phase ──────
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

    # ── Validate KB answer ──────────────────────────────────────────────
    #   kb_valid / ai_generated → END (awaiting self-or-human next turn)
    #   not_in_kb               → check_duplicates
    #   too_vague               → END (awaiting retry clarification)
    workflow.add_conditional_edges(
        "validate_kb_answer",
        route_after_kb_validation,
        {
            "check_duplicates": "check_duplicates",
            "__end__": END,
        },
    )

    # ── Self-or-human choice ────────────────────────────────────────────
    #   self       → END
    #   ticket     → check_duplicates
    #   follow-up  → kb_search_agent (fresh search on the new question)
    workflow.add_conditional_edges(
        "self_or_human_handler",
        route_after_self_or_human,
        {
            "check_duplicates": "check_duplicates",
            "kb_search_agent": "kb_search_agent",
            "__end__": END,
        },
    )

    # ── Duplicate check ─────────────────────────────────────────────────
    #   duplicate found → END
    #   no duplicate    → draft_ticket
    workflow.add_conditional_edges(
        "check_duplicates",
        route_after_duplicate_check,
        {
            "draft_ticket": "draft_ticket",
            "__end__": END,
        },
    )

    # ── draft_ticket → END (awaiting confirmation) ───────────────────────
    # Single-shot node: classification happens via
    # classify_ticket_category_pipeline() before this prompt runs, so
    # there's no tool-call loop to route through anymore.
    workflow.add_edge("draft_ticket", END)

    # ── category_clarification_handler → END (awaiting confirmation) ────
    # Re-entry point when draft_ticket asked a confidence-check clarifying
    # question — always ends in a ticket draft (never a second question,
    # see category_clarification_handler()'s docstring), so this is a
    # single-shot node exactly like draft_ticket.
    workflow.add_edge("category_clarification_handler", END)

    # ── Confirm category → save or re-ask ───────────────────────────────
    workflow.add_conditional_edges(
        "confirm_category_handler",
        route_after_category_confirmation,
        {
            "save_ticket": "save_ticket",
            "__end__": END,
        },
    )

    # ── Save ticket → END ──────────────────────────────────────────────
    workflow.add_edge("save_ticket", END)

    # ── ticket_status_agent ↔ ticket_status_tools loop ────────────────────
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
