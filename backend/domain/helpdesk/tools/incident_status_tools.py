"""
check_incident_status tool for ticket_status_agent.py — replaces the old
local-DB get_user_tickets lookup with a real call to SLT's incident CRM
(QueryIncident REST endpoint), looked up by incident ID rather than by
user_id (this API has no per-customer listing — see docstring below).

Config is read directly via os.getenv() (not core/config.py — this module
stays entirely within domain/helpdesk/, see .env's "SLT Incident Status
API" section for the three vars it needs).
"""

import logging
import os
from typing import Annotated, Any, Optional

import httpx
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

log = logging.getLogger(__name__)

_API_URL = os.getenv("SLT_INCIDENT_API_URL", "")
_CLIENT_ID = os.getenv("SLT_INCIDENT_API_CLIENT_ID", "")
_COOKIE = os.getenv("SLT_INCIDENT_API_COOKIE", "")

_REQUEST_TIMEOUT_SECONDS = 15


def _is_empty(value: Any) -> bool:
    """True for the API's "no value" convention: missing, None, {}, or ''.

    Unlike a typical REST API, an unset field here comes back as an empty
    JSON object ({}) rather than null or an omitted key.
    """
    if value is None:
        return True
    if isinstance(value, dict):
        return len(value) == 0
    if isinstance(value, str):
        return value.strip() == ""
    return False


def _field(incident: dict, key: str) -> Optional[str]:
    """Read one incident field, normalizing the {}-means-unset convention."""
    value = incident.get(key)
    return None if _is_empty(value) else value


def _latest_note(incident: dict) -> Optional[str]:
    """Extract the most recent note's text, if any.

    ListOfPubIncidentNote.PubIncidentNote is serialized by the upstream
    system as the SAME JSON key repeated once per note (not a real JSON
    array) — every standard parser, including Python's json/httpx, keeps
    only the LAST occurrence and silently drops the rest. That happens to
    line up with the incident's own LastUpdated timestamp (verified against
    a real response), so the note we're left with after normal parsing is
    the latest one — exactly what a status reply needs. Getting the full
    history would require a custom duplicate-key-preserving JSON parser,
    which isn't worth it just to show "what's the current status".
    """
    notes_wrapper = incident.get("ListOfPubIncidentNote")
    if not isinstance(notes_wrapper, dict):
        return None

    note = notes_wrapper.get("PubIncidentNote")
    if isinstance(note, list):
        note = note[-1] if note else None
    if not isinstance(note, dict):
        return None

    return _field(note, "Note")


def _format_incident_status(incident: dict, incident_id: str) -> str:
    """Render a customer-facing status summary.

    Deliberately excludes anything PII: the assignee's name (SLTAssignee*),
    and the incident's own contact fields (SLTEmail/SLTMobile) are never
    surfaced here, by design — only ticket/status metadata.
    """
    lines = [f"Ticket ID     : {_field(incident, 'ActivityUID') or incident_id}"]

    status = _field(incident, "Status")
    sub_status = _field(incident, "SubStatus")
    lines.append(f"Status        : {status or 'Unknown'}" + (f" ({sub_status})" if sub_status else ""))

    main_category = _field(incident, "IncidentType")
    sub_category = _field(incident, "IncidentSubType")
    if main_category:
        lines.append(f"Category      : {main_category}")
    if sub_category:
        lines.append(f"Sub-category  : {sub_category}")

    description = _field(incident, "Description")
    if description:
        lines.append(f"Description   : {description}")

    priority = _field(incident, "Priority")
    if priority:
        lines.append(f"Priority      : {priority}")

    reported = _field(incident, "DateReported") or _field(incident, "DateCreated")
    if reported:
        lines.append(f"Reported      : {reported}")

    last_updated = _field(incident, "LastUpdated")
    if last_updated:
        lines.append(f"Last Updated  : {last_updated}")

    latest_note = _latest_note(incident)
    if latest_note:
        lines.append(f"Latest Update : {latest_note}")

    return "\n".join(lines)


async def _fetch_incident(incident_id: str) -> Optional[dict]:
    """Call QueryIncident and return the PubHlsIncident dict, or None if the
    ID wasn't found / the response didn't have the expected shape."""
    payload = {
        "IncidentQueryByExample_Input": {
            "ListOfSltIncidentIntObj": {"PubHlsIncident": {"Id": incident_id}}
        }
    }
    headers = {
        "X-IBM-Client-Id": _CLIENT_ID,
        "Content-Type": "application/json",
    }
    if _COOKIE:
        headers["Cookie"] = _COOKIE

    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
        response = await client.post(_API_URL, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()

    incident = (
        data.get("IncidentQueryByExample_Output", {})
        .get("ListOfSltIncidentIntObj", {})
        .get("PubHlsIncident")
    )
    if isinstance(incident, list):
        incident = incident[0] if incident else None
    if not isinstance(incident, dict) or not incident:
        return None
    return incident


@tool
async def check_incident_status(
    incident_id: str,
    user_id: Annotated[str, InjectedState("user_id")] = "anonymous",
) -> str:
    """
    Look up the current status of ONE support incident by its ID, from
    SLT's live incident/CRM system (not the local ticket database).

    Only call this once you have an actual incident ID from the user —
    IDs look like "1-1HRRK5X" (a digit, a dash, then letters/digits). If
    the user hasn't given one yet, ask for it instead of calling this tool.

    Args:
        incident_id: The incident ID to look up, exactly as given by the user.
        user_id: Injected from state for logging only — hidden from the LLM.
    """
    incident_id = (incident_id or "").strip()
    if not incident_id:
        return "NO_INCIDENT_ID_GIVEN"

    if not _API_URL or not _CLIENT_ID:
        log.error("check_incident_status: SLT_INCIDENT_API_URL/CLIENT_ID not configured")
        return "INCIDENT_API_NOT_CONFIGURED"

    log.info("check_incident_status | user_id=%r incident_id=%r", user_id, incident_id)

    try:
        incident = await _fetch_incident(incident_id)
    except httpx.TimeoutException:
        log.warning("check_incident_status: timed out for incident_id=%r", incident_id)
        return "The incident lookup timed out. Please try again in a moment."
    except httpx.HTTPStatusError as exc:
        log.warning(
            "check_incident_status: HTTP %s for incident_id=%r",
            exc.response.status_code,
            incident_id,
        )
        return "Unable to reach the incident system right now. Please try again later."
    except Exception as exc:
        log.error("check_incident_status: unexpected error for incident_id=%r: %s", incident_id, exc)
        return "Something went wrong while looking up that incident. Please try again later."

    if incident is None:
        return f"NO_INCIDENT_FOUND for ID '{incident_id}'. Ask the user to double-check the ID."

    return _format_incident_status(incident, incident_id)
