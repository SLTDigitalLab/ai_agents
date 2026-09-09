"""
Research workflow layer 2: knowledge-base search + answer validation.

kb_search_agent searches the Qdrant knowledge base once no solved ticket
matched (see research.py for layer 1); validate_kb_answer then decides
whether that answer was good enough, needs clarification, or means "not in
the KB — make a ticket" (handed off to duplicates.check_duplicates).
"""

from typing import Literal

from langchain_core.messages import AIMessage

from domain.state import AgentState
from domain.helpdesk.pipeline.helpers import (
    llm,
    _message_to_text,
    _latest_user_message,
    _continues_prior_reply,
    _is_query_too_vague,
)
from domain.helpdesk.prompts import kb_search_system_prompt, vague_query_clarification_system_prompt
from domain.helpdesk.tools.helpdesk_tools import kb_llm, kb_search_llm


# [NODE] Layer 2: searches the Qdrant knowledge base via the
# search_knowledge_base tool and answers from the results. Reached when
# no solved ticket matched. Also the re-entry point for the
# awaiting_self_or_human / awaiting_category_confirmation phases, where it
# just forwards on without searching again.
async def kb_search_agent(state: AgentState) -> dict:
    """
    Entry point for the KB-search-and-beyond layer: a fresh research_agent
    handoff, or a continuation of the self-or-human / category-confirmation
    phases. Those continuations are dispatched onward by should_continue_kb_search
    without any LLM call here; only a fresh handoff triggers a KB search.

    Turn 1 — LLM emits tool_call for search_knowledge_base
             → graph routes to kb_search_tools
    Turn 2 — ToolMessage has appended a ToolMessage with KB results
             → LLM reads it and produces a final answer → END
    """
    ticket_phase = state.get("helpdesk_ticket_phase", "")
    if ticket_phase in (
        "awaiting_self_or_human",
        "awaiting_category_confirmation",
        "awaiting_category_clarification",
    ):
        print(f"[kb_search_agent] ticket_phase={ticket_phase!r} → dispatching onward")
        return {}

    original_query = state.get("helpdesk_original_query", "") or _latest_user_message(
        state
    )
    agent_id = state.get("agent_id", "helpdesk")
    user_id = state.get("user_id", "anonymous")
    messages = state.get("messages", [])

    print(
        f"[kb_search_agent] user_id={user_id!r} agent_id={agent_id!r} query={original_query!r}"
    )

    system_prompt = {
        "role": "system",
        "content": kb_search_system_prompt(original_query),
    }

    # The last message being a ToolMessage FROM search_knowledge_base means
    # kb_search_tools already ran earlier in this same mini-loop — so this is
    # Turn 2 (reading the result), not a fresh search. Checking the message
    # *type* alone isn't enough: at the start of a fresh handoff the last
    # message is often already a ToolMessage from research_agent's unrelated
    # search_solved_tickets_tool call, which would otherwise be mistaken for
    # "the KB search already happened".
    last_message = messages[-1] if messages else None
    kb_search_already_ran = (
        getattr(last_message, "type", "") == "tool"
        and getattr(last_message, "name", "") == "search_knowledge_base"
    )

    if not kb_search_already_ran and _is_query_too_vague(original_query):
        # Catch queries too thin to search on BEFORE running the forced KB
        # search + "not found" reply, instead of after. Doing this check
        # post-hoc (as validate_kb_answer's vague-query override still does,
        # below, as a defense-in-depth fallback) meant the user saw
        # kb_search_agent's "not found... let me create a support ticket"
        # message and then, moments later, a second message walking that
        # back to ask for details instead — a self-contradictory run-on
        # observed live 2026-08-11/12. Short-circuiting here means this is
        # the ONLY message sent this turn, so continuation=False (nothing
        # has streamed yet for this turn at this point).
        retry_count = state.get("helpdesk_retry_count", 0)
        if retry_count < 2:
            print(
                "[kb_search_agent] vague query pre-check → awaiting_retry_clarification "
                f"({original_query!r})"
            )
            response = await llm.ainvoke([
                {
                    "role": "system",
                    "content": vague_query_clarification_system_prompt(
                        original_query, continuation=False
                    ),
                },
            ])
            return {
                "messages": [AIMessage(content=_message_to_text(response))],
                "helpdesk_ticket_phase": "awaiting_retry_clarification",
                "helpdesk_retry_count": retry_count + 1,
            }
        print("[kb_search_agent] vague query pre-check → creating_ticket (max retries)")
        return {
            "helpdesk_ticket_phase": "creating_ticket",
            "helpdesk_retry_count": 0,
        }

    if not kb_search_already_ran:
        # Turn 1 — tool_choice is forced, so the model cannot skip straight
        # to a reply without ever querying Qdrant.
        response = await kb_search_llm.ainvoke([system_prompt, *messages])
        print(f"[kb_search_agent] Turn 1 tool_calls={getattr(response, 'tool_calls', None)}")
        return {"messages": [response]}

    # Turn 2 — tool result is in messages; auto tool-choice so the model
    # just answers instead of being forced to call the tool again.
    response = await kb_llm.ainvoke([system_prompt, *messages])
    print(f"[kb_search_agent] reply={_message_to_text(response)[:100]!r}...")
    return {"messages": [response]}


# [ROUTER] Decides what happens after kb_search_agent runs: execute its
# search tool call, validate the answer it just gave, or dispatch to
# whichever mid-flow handler (self-or-human / category confirmation) is active.
def should_continue_kb_search(
    state: AgentState,
) -> Literal[
    "kb_search_tools",
    "validate_kb_answer",
    "self_or_human_handler",
    "confirm_category_handler",
    "category_clarification_handler",
    "check_duplicates",
    "__end__",
]:
    """
    After kb_search_agent runs:
      - phase == awaiting_self_or_human          → self_or_human_handler
      - phase == awaiting_category_confirmation  → confirm_category_handler
      - phase == awaiting_category_clarification → category_clarification_handler
      - phase == awaiting_retry_clarification    → __end__ (the vague-query
                                                     pre-check above already
                                                     asked its question;
                                                     wait for the user's reply)
      - phase == creating_ticket                 → check_duplicates (the
                                                     vague-query pre-check hit
                                                     its retry cap)
      - tool_calls present                       → execute search_knowledge_base
      - plain reply                              → validate the KB answer quality
    """
    ticket_phase = state.get("helpdesk_ticket_phase", "")
    if ticket_phase == "awaiting_self_or_human":
        print("[router] awaiting self-or-human → self_or_human_handler")
        return "self_or_human_handler"
    if ticket_phase == "awaiting_category_confirmation":
        print("[router] awaiting category confirmation → confirm_category_handler")
        return "confirm_category_handler"
    if ticket_phase == "awaiting_category_clarification":
        print("[router] awaiting category clarification → category_clarification_handler")
        return "category_clarification_handler"
    if ticket_phase == "awaiting_retry_clarification":
        print("[router] vague-query pre-check asked for details → ending (awaiting reply)")
        return "__end__"
    if ticket_phase == "creating_ticket":
        print("[router] vague-query pre-check hit retry cap → checking duplicates")
        return "check_duplicates"

    last_message = state["messages"][-1]
    if getattr(last_message, "tool_calls", None):
        print("[kb_search_agent] tool call detected → running kb_search_tools")
        return "kb_search_tools"
    print("[kb_search_agent] no tool call → validating KB answer")
    return "validate_kb_answer"


# [NODE] Decides, via string-matching heuristics on kb_search_agent's own
# reply (no LLM call in the common case), whether the KB answer was good
# enough, needs clarification (up to 2 retries), or means "not in the KB —
# make a ticket". The one exception is the vague-query override below,
# which does make an LLM call to ask for details — see its comment.
async def validate_kb_answer(state: AgentState) -> dict:
    """Route after KB search using Python heuristics instead of an LLM call.

    kb_search_agent already told the user what it found (or didn't find) and
    included the appropriate follow-up question in its streamed response.
    In the common case this node only determines the next routing step — it
    adds no new message and makes no LLM call, so nothing extra streams to
    the user. The one exception is the vague-query override (see below):
    when the "not in KB" verdict is actually because the user's query was
    too thin to search on, this node asks for details itself instead of
    handing back a routing decision alone.
    """
    messages_list = state.get("messages", [])
    original_query = state.get("helpdesk_original_query", "")

    tool_result = ""
    ai_answer = ""
    for msg in reversed(messages_list):
        if not tool_result and getattr(msg, "type", "") == "tool":
            tool_result = _message_to_text(msg)
        if not ai_answer and getattr(msg, "type", "") == "ai":
            ai_answer = _message_to_text(msg)
        if tool_result and ai_answer:
            break

    ai_lower = ai_answer.lower()

    no_info_phrases = [
        "wasn't able to find",
        "unable to find",
        "can't find",
        "cannot find",
        "no specific information",
        "no relevant information",
        "no information found",
        "couldn't find",
        "could not find",
        "no information about",
        # NOTE: "let me help you create"/"help you create a support ticket"
        # used to be in this list but were removed — they're generic enough
        # to also appear in a genuinely FOUND answer that helpfully offers
        # ticket creation as a next step (e.g. "If you want, I can help you
        # create a support ticket so the team can investigate further").
        # That false-positived the whole answer into "not in KB" and jumped
        # straight to drafting a ticket in the same turn, before the user
        # ever got to answer the "Would you like to: 1/2" choice the found
        # answer had just asked. kb_search_system_prompt mandates the
        # not-found branch start with the exact phrase "wasn't able to
        # find specific information about that", which the remaining
        # phrases above already catch — no need for the broader ones.
    ]
    clarification_phrases = [
        "more details",
        "more specific",
        "could you provide",
        "provide more",
        "clarify",
        "more information about",
        "tell me more",
    ]

    if any(p in ai_lower for p in no_info_phrases) or len(tool_result.strip()) < 50:
        # Before giving up and creating a ticket, make sure the user's
        # original query actually had enough substance to search or
        # ticket on in the first place. A query like "issue" will never
        # match anything in the KB, and kb_search_agent's own reply can't
        # be trusted to always phrase that as "please clarify" instead of
        # "not found" — so check independently rather than assuming a
        # "not found" verdict means the KB was genuinely searched in vain.
        #
        # DEFENSE-IN-DEPTH ONLY: kb_search_agent now runs this same
        # _is_query_too_vague check *before* the KB search, short-circuiting
        # straight to a single clarification message without ever emitting
        # the "not found" reply — see kb_search_agent's vague-query
        # pre-check above. So on the normal fresh-handoff path, reaching
        # here already implies original_query passed that check, and this
        # branch is unreachable in practice. It stays as a safety net for
        # any future caller of validate_kb_answer that skips the pre-check.
        if _is_query_too_vague(original_query):
            retry_count = state.get("helpdesk_retry_count", 0)
            if retry_count < 2:
                print(
                    "[validate_kb_answer] → awaiting_retry_clarification "
                    f"(vague query override: {original_query!r})"
                )
                # Goes through the LLM (not a hardcoded string) so it
                # actually streams to the client: kb_search_agent's own
                # LLM call already streamed tokens earlier in this same
                # turn, so a plain Python-returned message here would be
                # silently swallowed (chat.py's non-streaming fallback only
                # fires when *nothing* streamed all turn). continuation=True
                # (kb_search_agent's reply always precedes this in the same
                # turn) so the two don't run on into each other — this is
                # the exact run-on the pre-check above now avoids in the
                # common case (observed live 2026-08-11/12).
                response = await llm.ainvoke([
                    {
                        "role": "system",
                        "content": vague_query_clarification_system_prompt(
                            original_query, continuation=_continues_prior_reply(state)
                        ),
                    },
                ])
                return {
                    "messages": [AIMessage(content=_message_to_text(response))],
                    "helpdesk_ticket_phase": "awaiting_retry_clarification",
                    "helpdesk_retry_count": retry_count + 1,
                }
            print("[validate_kb_answer] → creating_ticket (vague query, max retries)")
            return {
                "helpdesk_ticket_phase": "creating_ticket",
                "helpdesk_retry_count": 0,
            }
        print("[validate_kb_answer] → creating_ticket (not_in_kb heuristic)")
        return {"helpdesk_ticket_phase": "creating_ticket"}

    if any(p in ai_lower for p in clarification_phrases):
        retry_count = state.get("helpdesk_retry_count", 0)
        if retry_count >= 2:
            print("[validate_kb_answer] → creating_ticket (max retries)")
            return {
                "helpdesk_ticket_phase": "creating_ticket",
                "helpdesk_retry_count": 0,
            }
        print("[validate_kb_answer] → awaiting_retry_clarification")
        return {
            "helpdesk_ticket_phase": "awaiting_retry_clarification",
            "helpdesk_retry_count": retry_count + 1,
        }

    print("[validate_kb_answer] → awaiting_self_or_human (kb_valid heuristic)")
    return {"helpdesk_ticket_phase": "awaiting_self_or_human"}


# [ROUTER] Reads validate_kb_answer's verdict: not in KB → check for a
# duplicate ticket before drafting one; otherwise end and wait for the
# user's reply to the question the KB answer already asked them.
def route_after_kb_validation(
    state: AgentState,
) -> Literal["check_duplicates", "__end__"]:
    """After validate_kb_answer: creating_ticket → check duplicates, else wait."""
    phase = state.get("helpdesk_ticket_phase", "")
    if phase == "creating_ticket":
        print("[router] not in KB → checking duplicates")
        return "check_duplicates"
    print("[router] KB answer presented → waiting for user choice")
    return "__end__"
