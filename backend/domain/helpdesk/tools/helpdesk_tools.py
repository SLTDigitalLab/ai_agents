"""
Tool definitions for the helpdesk agent graph
(backend/domain/helpdesk/pipeline/).
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from langchain_core.tools import tool

from core.llm import get_chat_model
from services.helpdesk_tickets import (
    list_helpdesk_tickets,
    search_solved_tickets,
    list_categories,
)
from domain.tools.rag_tools import search_knowledge_base

llm = get_chat_model()

_SRI_LANKA_TZ = ZoneInfo("Asia/Colombo")


# [HELPER] helpdesk_tickets.createdAt/updatedAt are TIMESTAMPTZ columns —
# psycopg hands them back as UTC-aware datetimes. Left as-is, the LLM just
# echoes the raw UTC value (and sometimes labels it "UTC" itself), which
# reads wrong to a Sri Lanka-based user/support team. Convert before it
# ever reaches the model instead of hoping the LLM does time-zone math.
def _format_sri_lanka_time(value) -> str:
    """Render a UTC timestamp (datetime or ISO string) in Sri Lanka time."""
    if value is None:
        return "N/A"

    dt = value
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except ValueError:
            return value  # unparseable — show as-is rather than crash

    if not isinstance(dt, datetime):
        return str(value)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)  # DB default is UTC

    local_dt = dt.astimezone(_SRI_LANKA_TZ)
    return local_dt.strftime("%Y-%m-%d %H:%M:%S") + " (Sri Lanka Time, UTC+5:30)"


@tool
def get_user_tickets(
    user_id: str,
    status: str | None = None,
) -> str:
    """
    Fetch all helpdesk tickets for a given user from the database.
    Optionally filter by status (e.g. 'open', 'closed', 'pending').
    Returns a formatted summary of the user's tickets.
    Call this whenever the user asks about their tickets, issues, or requests.
    """
    try:
        tickets = list_helpdesk_tickets(user_id=user_id, status=status)
    except Exception as exc:
        return f"Error fetching tickets: {exc}"

    if not tickets:
        return f"No tickets found for user '{user_id}'."

    lines: list[str] = [f"Found {len(tickets)} ticket(s) for user '{user_id}':\n"]
    for t in tickets:
        lines.append(
            f"- Ticket ID     : {t.get('ticket_id') or 'N/A'}\n"
            f"  Status        : {t.get('status') or 'N/A'}\n"
            f"  Message       : {t.get('message') or 'N/A'}\n"
            f"  Main Category : {t.get('main_category') or 'N/A'}\n"
            f"  Sub-category  : {t.get('sub_category') or 'N/A'}\n"
            f"  Duplicate     : {t.get('duplicate_check') or 'N/A'}\n"
            f"  Needs Info    : {t.get('need_more_informations') or 'N/A'}\n"
            f"  Created       : {_format_sri_lanka_time(t.get('createdAt'))}\n"
            f"  Updated       : {_format_sri_lanka_time(t.get('updatedAt'))}"
        )
    return "\n\n".join(lines)


@tool
def search_solved_tickets_tool(query: str) -> str:  # noqa: ARG001
    """
    Search the solved helpdesk tickets database for previously resolved issues.
    Call this with the user's question to find if a similar issue has already
    been solved. Returns a list of solved tickets with their requirements and
    answers.
    """
    try:
        tickets = search_solved_tickets(limit=50)
    except Exception as exc:
        return f"Error searching solved tickets: {exc}"

    if not tickets:
        return "NO_SOLVED_TICKETS_FOUND"

    lines: list[str] = [f"Found {len(tickets)} solved ticket(s):\n"]
    for t in tickets:
        lines.append(
            f"- Ticket #{t.get('id', 'N/A')}\n"
            f"  Requirement: {t.get('requirement', 'N/A')}\n"
            f"  Answer: {t.get('answer', 'N/A')}"
        )
    return "\n\n".join(lines)


@tool
def present_solved_answer(matched: bool, answer: str = "") -> str:
    """
    Report your verdict after reviewing search_solved_tickets_tool's results.
    Call this exactly once, right after that search returns, to communicate
    your finding — never reply in plain text at that step instead, since the
    verdict itself is not a message meant for the user's eyes.

    Args:
        matched: True if a solved ticket clearly resolves the user's current
            issue, False if none of the results address it.
        answer: Required when matched=True — your full, conversational,
            user-facing reply, rewritten in your own words from the matching
            solved ticket. Leave empty when matched=False.
    """
    return "acknowledged"


@tool
def list_categories_tool(category_name: str | None = None) -> str:
    """
    Fetch the available helpdesk ticket categories and subcategories from the
    all_categories table. Call this before classifying a user's issue into a
    category, to see the valid main-category / sub-category options.
    Optionally filter by category_name.
    """
    try:
        categories = list_categories(category_name=category_name)
    except Exception as exc:
        return f"Error fetching categories: {exc}"

    if not categories:
        return "No predefined categories available."

    lines: list[str] = [
        f"Found {len(categories)} categor{'y' if len(categories) == 1 else 'ies'}:\n"
    ]
    for c in categories:
        description = c.get("description") or ""
        keywords = c.get("keywords") or ""
        suffix = f": {description}" if description else ""
        if keywords:
            suffix += f" (keywords: {keywords})"
        lines.append(
            f"- {c.get('category_name', 'N/A')} → {c.get('subcategory', 'N/A')}{suffix}"
        )
    return "\n".join(lines)


# ── Tool sets & LLM bindings ─────────────────────────────────────────────

TICKET_TOOLS = [get_user_tickets]
SOLVED_TICKET_TOOLS = [search_solved_tickets_tool]
KB_TOOLS = [search_knowledge_base]
CATEGORY_TOOLS = [list_categories_tool]

ticket_llm = llm.bind_tools(TICKET_TOOLS)
kb_llm = llm.bind_tools(KB_TOOLS)
category_llm = llm.bind_tools(CATEGORY_TOOLS)

# kb_search_agent's search turn MUST call search_knowledge_base — auto tool
# choice (kb_llm above) lets the model skip the tool entirely, which is
# exactly what happens in practice: by the time kb_search_agent runs, the
# message history already contains an unrelated search_solved_tickets_tool
# call/result from research_agent, and the model mistakes that for "the
# search already happened" and answers straight from its own knowledge (or
# straight to the not-found script) instead of ever querying Qdrant. Forcing
# tool_choice on the search turn removes that ambiguity; the follow-up turn
# that reads the real tool result still uses the auto-choice kb_llm above.
kb_search_llm = llm.bind_tools(KB_TOOLS, tool_choice="search_knowledge_base")

# research_agent's two turns each get their OWN binding, each exposing only
# ONE tool. This isn't just prompt discipline — the model has no other tool
# schema available at either stage, so it physically cannot call the wrong
# one, and (for the verdict turn) cannot emit a streamable plain-text reply
# containing an internal-only routing decision instead of calling the tool.
solved_ticket_search_llm = llm.bind_tools([search_solved_tickets_tool])
solved_ticket_verdict_llm = llm.bind_tools([present_solved_answer])
