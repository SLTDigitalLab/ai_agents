"""Helpers for forwarding qualified form submissions to the SLT SMECMA BizLeads API."""

from __future__ import annotations

import logging
from typing import Any, Literal

import httpx


logger = logging.getLogger(__name__)

BIZLEADS_API_URL = "https://smecxapp.slt.lk/bizleads-api.php"
BizleadsResult = Literal["success", "duplicate", "network_error", "server_error", "skipped"]


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


async def submit_bizlead(
    *,
    name: str,
    phone: str,
    email: str,
    city: str,
    product: str,
    note: str,
) -> BizleadsResult:
    """Send a lead to BizLeads using form-encoded POST data.

    Returns one of:
      "success"        - lead accepted
      "duplicate"       - BizLeads returned "0" (already exists, not a failure)
      "network_error"   - couldn't reach BizLeads at all (DNS, timeout, connection refused)
      "server_error"    - BizLeads responded but with a non-200 status
      "skipped"         - required fields were missing, never attempted

    Never raises — callers that treat this as best-effort can ignore the
    result; callers that need to know exactly what happened can inspect it.
    """

    payload = {
        "name": _clean_text(name),
        "phone": _clean_text(phone),
        "email": _clean_text(email),
        "city": _clean_text(city),
        "product": _clean_text(product),
        "note": _clean_text(note),
    }

    if not all(payload.values()):
        logger.info("Skipping BizLeads submission because required fields are missing: %s", payload)
        return "skipped"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(BIZLEADS_API_URL, data=payload)
    except httpx.RequestError:
        logger.exception(
            "BizLeads network error for phone=%s product=%s",
            payload["phone"],
            payload["product"],
        )
        return "network_error"

    body = response.text.strip()

    if response.status_code != 200:
        logger.warning(
            "BizLeads returned HTTP %s for phone=%s product=%s",
            response.status_code,
            payload["phone"],
            payload["product"],
        )
        return "server_error"

    if body == "0":
        logger.info(
            "BizLeads duplicate lead skipped for phone=%s product=%s",
            payload["phone"],
            payload["product"],
        )
        return "duplicate"

    logger.info(
        "BizLeads submission completed for phone=%s product=%s",
        payload["phone"],
        payload["product"],
    )
    return "success"