"""
Enterprise router - receives service-request leads from the React
frontend and forwards them to the Bitrix24 CRM via webhook.

POST /api/v1/enterprise/lead
"""

import httpx
from fastapi import APIRouter, HTTPException

from core.config import settings
from schemas.enterprise import EnterpriseLead

router = APIRouter(prefix="/api/v1/enterprise", tags=["Enterprise"])


@router.post("/lead")
async def create_lead(payload: EnterpriseLead):
    """Accept an enterprise lead and push it to Bitrix24 CRM."""

    webhook_url = settings.BITRIX24_WEBHOOK_URL
    if not webhook_url:
        raise HTTPException(
            status_code=500,
            detail="BITRIX24_WEBHOOK_URL is not configured on the server.",
        )

    # ── Map Pydantic model → Bitrix24 fields ─────────────────────────────
    bitrix_payload = {
        "fields[TITLE]": f"{payload.company_name} - {payload.select_service}",
        "fields[STATUS_ID]": "NEW",
        "fields[ASSIGNED_BY_ID]": "510",
        "fields[UF_CRM_1692256014]": payload.company_name,
        "fields[UF_CRM_1692255317548]": payload.contact_person,
        "fields[UF_CRM_1692256034]": payload.contact_number,
        "fields[UF_CRM_64DDC37491441]": payload.select_service,
        "fields[COMMENTS]": (
            f"BRN: {payload.business_registration_number or 'N/A'} | "
            f"Remarks: {payload.remarks or 'N/A'}"
        ),
    }

    # ── POST to Bitrix24 ─────────────────────────────────────────────────
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(webhook_url, data=bitrix_payload)

    if response.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Bitrix24 returned status {response.status_code}",
        )

    return {
        "status": "success",
        "message": "Lead submitted to Bitrix24 successfully.",
        "bitrix_response": response.json(),
    }
