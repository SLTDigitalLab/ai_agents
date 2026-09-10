"""
First step of ticket creation: dedupe against the user's existing open
tickets before ever drafting a new one (see ticket_draft.py for the next
step).
"""

import re
from typing import Literal

from langchain_core.messages import AIMessage

from domain.state import AgentState
from domain.helpdesk.pipeline.helpers import llm, _message_to_text, _latest_user_message, _continues_prior_reply
from domain.helpdesk.prompts import duplicate_found_system_prompt
from services.helpdesk_tickets import list_helpdesk_tickets


# Compares the issue text against the user's existing open tickets
# (Jaccard word-overlap, no LLM) to avoid creating a duplicate.
async def check_duplicates(state: AgentState) -> dict:
    """Check for duplicate open tickets. If one is found, an LLM writes a
    friendly notification for the user."""
    user_id = state.get("user_id", "anonymous")
    original_query = state.get("helpdesk_original_query", "") or _latest_user_message(
        state
    )
    continuation = _continues_prior_reply(state)

    try:
        open_tickets = list_helpdesk_tickets(user_id=user_id, status="open")
    except Exception:
        open_tickets = []

    if not open_tickets:
        print("[check_duplicates] no open tickets → proceed to draft")
        return {"helpdesk_ticket_phase": "no_duplicate"}

    query_words = set(re.sub(r"[^\w\s]", "", original_query.lower()).split())

    for t in open_tickets[:10]:
        ticket_words = set(re.sub(r"[^\w\s]", "", t.get("message", "").lower()).split())
        union = query_words | ticket_words
        if not union:
            continue
        similarity = len(query_words & ticket_words) / len(union)
        if similarity > 0.35:
            print(
                f"[check_duplicates] duplicate found similarity={similarity:.2f} → {t.get('ticket_id')}"
            )
            dup_text = (
                f"Ticket ID: {t.get('ticket_id', 'N/A')}, "
                f"Status: {t.get('status', 'N/A')}, "
                f"Message: {t.get('message', 'N/A')}, "
                f"Category: {t.get('main_category', 'N/A')} / {t.get('sub_category', 'N/A')}, "
                f"Created: {t.get('createdAt', 'N/A')}"
            )
            response = await llm.ainvoke(
                [
                    {
                        "role": "system",
                        "content": duplicate_found_system_prompt(
                            dup_text, continuation=continuation
                        ),
                    },
                    {"role": "user", "content": "Is my issue already being tracked?"},
                ]
            )
            return {
                "messages": [AIMessage(content=_message_to_text(response))],
                "helpdesk_ticket_phase": "",
                "helpdesk_research_phase": "",
                "helpdesk_original_query": "",
                "helpdesk_category_clarify_count": 0,
            }

    print("[check_duplicates] no duplicate → proceed to draft")
    return {"helpdesk_ticket_phase": "no_duplicate"}


# [ROUTER] No duplicate found → draft a new ticket; duplicate found →
# check_duplicates already told the user, so just end.
def route_after_duplicate_check(
    state: AgentState,
) -> Literal["draft_ticket", "__end__"]:
    """After check_duplicates: no duplicate → draft ticket, else END."""
    phase = state.get("helpdesk_ticket_phase", "")
    if phase == "no_duplicate":
        print("[router] no duplicate → drafting ticket")
        return "draft_ticket"
    print("[router] duplicate found → ending")
    return "__end__"
