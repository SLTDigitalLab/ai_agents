"""Helpers for forwarding qualified form submissions to the SLT SMECMA BizLeads API."""

from __future__ import annotations

import logging
from typing import Any

import httpx


logger = logging.getLogger(__name__)

BIZLEADS_API_URL = "https://smecxapp.slt.lk/bizleads-api.php"


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
) -> None:
    """Send a lead to BizLeads using form-encoded POST data.

    The API returns ``0`` for a duplicate submission and may return an empty
    body for success. This helper treats the call as best-effort so the existing
    email/Bitrix flows keep working even if BizLeads is temporarily unavailable.
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
        return

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(BIZLEADS_API_URL, data=payload)

        body = response.text.strip()
        if response.status_code != 200:
            logger.warning(
                "BizLeads returned HTTP %s for phone=%s product=%s",
                response.status_code,
                payload["phone"],
                payload["product"],
            )
            return

        if body == "0":
            logger.info(
                "BizLeads duplicate lead skipped for phone=%s product=%s",
                payload["phone"],
                payload["product"],
            )
            return

        logger.info(
            "BizLeads submission completed for phone=%s product=%s",
            payload["phone"],
            payload["product"],
        )
    except Exception:
        logger.exception(
            "BizLeads submission failed for phone=%s product=%s",
            payload["phone"],
            payload["product"],
        )
