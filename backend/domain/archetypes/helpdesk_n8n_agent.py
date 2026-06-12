"""
Archetype 5 - Helpdesk → n8n Forwarding workflow

This module defines a tiny LangGraph workflow that forwards the latest
user message (plus optional conversation context) to an n8n webhook URL
configured via `core.config.settings.N8N_HELPDESK_WEBHOOK_URL`.
"""

from typing import Any
import httpx
from langchain_core.messages import AIMessage
from langgraph.graph import START, END, StateGraph
from core.config import settings
from domain.state import AgentState


def _message_to_text(message: Any) -> str:
    """Extract a plain-text string from a LangChain message object or raw value."""

    # Get message content safely.
    content = getattr(message, "content", message)
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and "text" in block:
                parts.append(str(block["text"]))
        return " ".join(p.strip() for p in parts if p).strip()
    if content is None:
        return ""
    return str(content).strip()


def _format_markdown_table(rows: list[dict[str, Any]]) -> str:
    """Render a simple markdown table from a list of dictionaries."""
    if not rows:
        return ""

    # Collect all table columns.
    columns: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in columns:
                columns.append(str(key))

    if not columns:
        return ""

    def _cell(value: Any) -> str:
        text = _message_to_text(value)
        return text.replace("|", r"\|") or "-"

    header = " | ".join(columns)
    separator = " | ".join("---" for _ in columns)
    body = [" | ".join(_cell(row.get(column, "")) for column in columns) for row in rows]

    return "\n".join([header, separator, *body])


def _format_n8n_payload(payload: Any) -> str:
    """Normalize common n8n response shapes into a single markdown reply."""
    if isinstance(payload, list):
        # Format each item in the list.
        parts = [_format_n8n_payload(item) for item in payload]
        return "\n\n".join(part for part in parts if part).strip()

    if isinstance(payload, dict):
        if "display_text" in payload or "data_table" in payload or "num" in payload:
            parts: list[str] = []

            # Add main reply text.
            display_text = payload.get("display_text")
            if display_text:
                parts.append(_message_to_text(display_text))

            data_table = payload.get("data_table")
            # Add table data if n8n sends it.
            if isinstance(data_table, list) and data_table:
                first_row = data_table[0]
                if isinstance(first_row, dict):
                    table_text = _format_markdown_table(
                        [row for row in data_table if isinstance(row, dict)]
                    )
                    if table_text:
                        parts.append(table_text)
                else:
                    table_lines = [f"- {_message_to_text(row)}" for row in data_table if row]
                    if table_lines:
                        parts.append("\n".join(table_lines))

            return "\n\n".join(part for part in parts if part).strip()

        # Check common reply keys.
        for key in ("reply", "message", "text", "response", "output", "result", "answer"):
            v = payload.get(key)
            if v:
                if isinstance(v, (dict, list)):
                    reply = _format_n8n_payload(v)
                    if reply:
                        return reply
                return str(v).strip()

        # Check nested response data.
        for nested_key in ("data", "output", "result", "response"):
            nested_value = payload.get(nested_key)
            if nested_value is not None:
                reply = _format_n8n_payload(nested_value)
                if reply:
                    return reply

    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload.strip()
    return str(payload).strip()


def _extract_nested_value(payload: Any, keys: tuple[str, ...]) -> Any:
    """Find the first matching value in a nested n8n response payload."""
    if isinstance(payload, list):
        # Search inside each list item.
        for item in payload:
            value = _extract_nested_value(item, keys)
            if value not in (None, ""):
                return value
        return None

    if not isinstance(payload, dict):
        return None

    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value

    # Search common nested fields.
    for nested_key in ("data", "output", "result", "response"):
        nested_value = payload.get(nested_key)
        value = _extract_nested_value(nested_value, keys)
        if value not in (None, ""):
            return value

    return None


def _extract_execution_id(payload: Any) -> str | None:
    value = _extract_nested_value(payload, ("executionId", "execution_id"))
    if value not in (None, ""):
        return str(value)

    # Some n8n replies keep the id here.
    if isinstance(payload, dict):
        execution = payload.get("$execution")
        if isinstance(execution, dict) and execution.get("id") not in (None, ""):
            return str(execution["id"])

    return None


def _extract_resume_url(payload: Any) -> str | None:
    value = _extract_nested_value(payload, ("resumeUrl", "resume_url"))
    return str(value) if value not in (None, "") else None


def _extract_waiting_for_input(payload: Any, resume_url: str | None) -> bool:
    value = _extract_nested_value(payload, ("waitingForInput", "waiting_for_input"))
    return bool(value) or resume_url is not None

# Send the user message to n8n.
async def forward_to_n8n(state: AgentState) -> dict:

    # Read webhook URL from settings.
    webhook_url = getattr(settings, "N8N_HELPDESK_WEBHOOK_URL", "")
    if not webhook_url:
        return {
            "messages": [
                AIMessage(
                    content=(
                        "Helpdesk is not configured. Set N8N_HELPDESK_WEBHOOK_URL in the backend "
                        "configuration to enable the helpdesk workflow."
                    )
                )
            ]
        }

    # Keep only recent messages.
    conversation = []
    for msg in state.get("messages", [])[-10:]:
        role = "assistant" if getattr(msg, "type", "") == "ai" else "user"
        conversation.append({"role": role, "content": _message_to_text(msg)})

    # Find the latest user message.
    latest_user = ""
    for msg in reversed(state.get("messages", [])):
        if getattr(msg, "type", "") in ("human", "user"):
            latest_user = _message_to_text(msg)
            break

    session_id = str(
        state.get("user_id")
        or state.get("agent_id")
        or "helpdesk"
    )

    # Get saved resume data.
    stored_execution_id = state.get("helpdesk_execution_id")
    stored_resume_url = state.get("helpdesk_resume_url")
    waiting_for_input = bool(state.get("helpdesk_waiting_for_input"))
    should_resume = waiting_for_input and bool(stored_resume_url)

    # Build request body for n8n.
    payload = {
        "sessionId": session_id,
        "userId": state.get("user_id", "anonymous"),
        "message": latest_user,
        "conversation": conversation,
    }

    if stored_execution_id:
        payload["executionId"] = stored_execution_id

    # Resume old workflow when needed.
    request_url = stored_resume_url if should_resume else webhook_url

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(request_url, json=payload)

        resp.raise_for_status()

        # Read response from n8n.
        response_payload: Any = None
        reply_text = resp.text.strip()
        try:
            response_payload = resp.json()
            reply_text = _format_n8n_payload(response_payload) or reply_text
        except Exception:
            pass

        execution_id = _extract_execution_id(response_payload) if response_payload is not None else None
        resume_url = _extract_resume_url(response_payload) if response_payload is not None else None
        waiting_for_input = _extract_waiting_for_input(response_payload, resume_url) if response_payload is not None else False

        if response_payload is not None:
            # Save resume data for next turn.
            stored_execution_id = execution_id or stored_execution_id
            stored_resume_url = resume_url or stored_resume_url
            if not waiting_for_input:
                # Clear resume data when workflow is done.
                stored_execution_id = None
                stored_resume_url = None

        # Return reply and updated state.
        state_update = {
            "messages": [AIMessage(content=reply_text or "Helpdesk workflow returned an empty response from n8n.")],
            "helpdesk_execution_id": stored_execution_id,
            "helpdesk_resume_url": stored_resume_url,
            "helpdesk_waiting_for_input": waiting_for_input,
        }

        return state_update

    except Exception as exc:  # pragma: no cover - network error handling
        return {
            "messages": [
                AIMessage(
                    content=(
                        "Helpdesk is unavailable right now. Please try again later. "
                        f"(Details: {exc})"
                    )
                )
            ]
        }


# Build the helpdesk workflow.
def build_helpdesk_n8n_workflow() -> StateGraph:

    workflow = StateGraph(AgentState)
    workflow.add_node("webhook", forward_to_n8n)
    workflow.add_edge(START, "webhook")
    workflow.add_edge("webhook", END)
    return workflow
