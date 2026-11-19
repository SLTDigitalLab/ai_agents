"""
Create incidents in SLT's incident CRM (CreateIncident REST endpoint).

The CRM generates the incident id itself and returns it as
Incident_Assign_Output.AssignId (e.g. "1-1VSMK1O"); any ActivityUID we send is
ignored (verified on staging 2026-11-18), so we don't send one. That id is the
same one QueryIncident looks up (see incident_status_tools.py), so it is what
the user gets as their ticket id.
"""

import logging
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from core.config import settings

log = logging.getLogger(__name__)

ALLOWED_PRIORITIES = ("Low", "Medium", "High")

# Fixed for every incident raised from the helpdesk form.
_DEFAULT_STATUS = "Assigned"
_DEFAULT_SYSTEM_ASSIGN_FLAG = "Y"

class CrmIncidentError(Exception):
    """CreateIncident failed. The message is safe to log, not to show users."""


class CrmNotConfiguredError(CrmIncidentError):
    """The CreateIncident URL or client id is missing from settings."""


@dataclass(frozen=True)
class CreatedIncident:
    incident_id: str
    raw: dict[str, Any]


def _is_empty(value: Any) -> bool:
    # The CRM returns {} (not null) for unset fields.
    if value is None:
        return True
    if isinstance(value, (dict, list)):
        return len(value) == 0
    if isinstance(value, str):
        return value.strip() == ""
    return False


def build_payload(
    *,
    description: str,
    incident_type: str,
    incident_sub_type: str,
    priority: str,
    sltapi_user_id: str,
) -> dict[str, Any]:
    return {
        "Incident_Assign_Input": {
            "ListOfSltIncidentIntObj": {
                "PubHlsIncident": {
                    "Description": description,
                    "IncidentType": incident_type,
                    "IncidentSubType": incident_sub_type,
                    "Priority": priority,
                    "Status": _DEFAULT_STATUS,
                    "SystemAsgnFlag": _DEFAULT_SYSTEM_ASSIGN_FLAG,
                    "SLTAPIUserID": sltapi_user_id,
                }
            }
        }
    }


def parse_response(data: Any) -> str:
    """Return the new incident id, or raise CrmIncidentError."""
    if not isinstance(data, dict):
        raise CrmIncidentError("CreateIncident returned a non-object body")
    output = data.get("Incident_Assign_Output")
    if not isinstance(output, dict):
        raise CrmIncidentError("CreateIncident response missing Incident_Assign_Output")

    error_code = output.get("Error_spcCode")
    error_message = output.get("Error_spcMessage")
    if not _is_empty(error_code) or not _is_empty(error_message):
        raise CrmIncidentError(
            f"CreateIncident error code={error_code!r} message={error_message!r}"
        )

    incident_id = output.get("AssignId")
    if _is_empty(incident_id) or not isinstance(incident_id, str):
        raise CrmIncidentError("CreateIncident response has no AssignId")
    return incident_id.strip()


async def create_incident(
    *,
    description: str,
    incident_type: str,
    incident_sub_type: str,
    priority: str,
    sltapi_user_id: str,
    client: Optional[httpx.AsyncClient] = None,
) -> CreatedIncident:
    """Create one incident and return its CRM id.

    Inputs are expected to be validated by the caller (router). Raises
    CrmNotConfiguredError, CrmIncidentError, or httpx errors (timeouts,
    connection failures, non-2xx) for the caller to map to a user message.
    """
    url = settings.SLT_INCIDENT_CREATE_API_URL
    client_id = settings.SLT_INCIDENT_API_CLIENT_ID
    if not url or not client_id:
        raise CrmNotConfiguredError(
            "SLT_INCIDENT_CREATE_API_URL / SLT_INCIDENT_API_CLIENT_ID not set"
        )
    if priority not in ALLOWED_PRIORITIES:
        raise ValueError(f"priority must be one of {ALLOWED_PRIORITIES}")

    payload = build_payload(
        description=description,
        incident_type=incident_type,
        incident_sub_type=incident_sub_type,
        priority=priority,
        sltapi_user_id=sltapi_user_id,
    )
    headers = {"X-IBM-Client-Id": client_id, "Content-Type": "application/json"}

    if client is None:
        timeout = settings.SLT_INCIDENT_CREATE_TIMEOUT_SECONDS
        async with httpx.AsyncClient(timeout=timeout) as own_client:
            response = await own_client.post(url, headers=headers, json=payload)
    else:
        response = await client.post(url, headers=headers, json=payload)

    response.raise_for_status()
    try:
        data = response.json()
    except ValueError as exc:
        raise CrmIncidentError("CreateIncident returned non-JSON body") from exc

    incident_id = parse_response(data)
    log.info(
        "CreateIncident ok | incident_id=%s user=%s type=%r",
        incident_id, sltapi_user_id, incident_type,
    )
    return CreatedIncident(incident_id=incident_id, raw=data)
