"""
Ticket form step: after check_duplicates finds no open duplicate, hand the
user a form instead of the old chat-based draft/category/confirm steps.

The form itself is rendered by the frontend (HelpdeskTicketForm.jsx) when it
sees the hidden marker below, and it submits straight to
POST /api/v1/helpdesk_dev/incidents, so the chat flow ends here.
"""

import json

from langchain_core.callbacks import adispatch_custom_event
from langchain_core.messages import AIMessage

from domain.state import AgentState
from domain.helpdesk.pipeline.helpers import _latest_user_message, _is_query_too_vague

# Parsed by the frontend; must match HELPDESK_FORM_OPEN/CLOSE in ChatInterface.jsx.
FORM_MARKER_OPEN = "[[HELPDESK_TICKET_FORM]]"
FORM_MARKER_CLOSE = "[[/HELPDESK_TICKET_FORM]]"

# Custom stream event that routers/chat.py forwards to the user verbatim.
# Needed because a plain AIMessage (no LLM call) is only shown when nothing
# else streamed this turn — and here the KB answer / "I'll check for an
# existing ticket" reply usually already has.
USER_TEXT_EVENT = "user_visible_text"

# Matches the description limit of the incidents endpoint / DB column.
_DESCRIPTION_MAX = 255

FORM_INTRO = (
    "I couldn't find an existing open ticket for this issue. "
    "Please fill in the form below to create your support ticket."
)


def build_form_message(description: str) -> str:
    # Strip "[[" / "]]" so user text can never break or forge the marker.
    prefill = description.replace("[[", "").replace("]]", "").strip()[:_DESCRIPTION_MAX]
    payload = json.dumps({"description": prefill}, ensure_ascii=False)
    return f"{FORM_INTRO}\n\n{FORM_MARKER_OPEN}{payload}{FORM_MARKER_CLOSE}"


async def show_ticket_form(state: AgentState) -> dict:
    """Send the ticket form (pre-filled with the user's issue) and end the flow."""
    # helpdesk_original_query holds the user's issue (self_or_human_handler
    # only overwrites it with a non-vague reply). Fall back to the latest
    # message only if it's more than a bare "2" / "create ticket".
    description = state.get("helpdesk_original_query", "") or ""
    if not description:
        latest = _latest_user_message(state)
        description = "" if _is_query_too_vague(latest) else latest
    text = build_form_message(description)

    # Leading blank line separates it from any reply already streamed this turn.
    await adispatch_custom_event(USER_TEXT_EVENT, {"text": "\n\n" + text})
    print(f"[show_ticket_form] form sent (prefill={bool(description)})")

    return {
        "messages": [AIMessage(content=text)],
        "helpdesk_ticket_phase": "",
        "helpdesk_research_phase": "",
        "helpdesk_original_query": "",
        "helpdesk_draft_ticket_id": "",
        "helpdesk_draft_main_category": "",
        "helpdesk_draft_sub_category": "",
        "helpdesk_draft_continuation": False,
        "helpdesk_retry_count": 0,
        "helpdesk_category_clarify_count": 0,
    }
