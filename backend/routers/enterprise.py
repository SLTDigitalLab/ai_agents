"""
Enterprise router - receives service-request leads from the React
frontend and forwards them to the Bitrix24 CRM via webhook.

POST /api/v1/enterprise/lead
"""

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from core.config import settings
from schemas.enterprise import EnterpriseLead
from services.bizleads import submit_bizlead

router = APIRouter(prefix="/api/v1/enterprise", tags=["Enterprise"])


def _has_bizleads_fields(payload: EnterpriseLead) -> bool:
    return all(
        [
            payload.company_name.strip(),
            payload.contact_person.strip(),
            payload.contact_number.strip(),
            payload.email.strip(),
            payload.city.strip() if payload.city else "",
            payload.select_service.strip(),
        ]
    )


def _parse_bitrix_response(response: httpx.Response):
    """Return JSON if Bitrix sends JSON, otherwise return the raw body text."""
    try:
        return response.json()
    except Exception:
        return response.text


@router.post("/lead")
async def create_lead(payload: EnterpriseLead, background_tasks: BackgroundTasks):
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
        "fields[UF_CRM_1692256050]": payload.email,
        "fields[UF_CRM_64DDC37491441]": payload.select_service,
        "fields[COMMENTS]": (
            f"BRN: {payload.business_registration_number or 'N/A'} | "
            f"City: {payload.city or 'N/A'} | "
            f"Remarks: {payload.remarks or 'N/A'}"
        ),
    }

    # ── POST to Bitrix24 ─────────────────────────────────────────────────
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(webhook_url, data=bitrix_payload)

    bitrix_response = _parse_bitrix_response(response)
    bitrix_ok = response.status_code == 200

    if not bitrix_ok:
        # Keep the user flow alive even if Bitrix is temporarily rejecting the lead.
        # The BizLeads handoff still runs so the submission is not blocked.
        bitrix_response = {
            "status": response.status_code,
            "body": bitrix_response,
        }

    if _has_bizleads_fields(payload):
        background_tasks.add_task(
            submit_bizlead,
            name=payload.contact_person,
            phone=payload.contact_number,
            email=payload.email,
            city=payload.city,
            product=payload.select_service,
            note=payload.note
            or (
                f"Company: {payload.company_name} | "
                f"BRN: {payload.business_registration_number or 'N/A'} | "
                f"Remarks: {payload.remarks or 'N/A'}"
            ),
        )

    if not bitrix_ok:
        return {
            "status": "partial_success",
            "message": "Enterprise lead was queued, but Bitrix24 returned a non-200 response.",
            "bitrix_response": bitrix_response,
        }

    return {
        "status": "success",
        "message": "Lead submitted to Bitrix24 successfully.",
        "bitrix_response": bitrix_response,
    }


@router.post("/test-webhook")
async def test_webhook(
    title: str = Query("Test_Lead", alias="fields[TITLE]"),
    status_id: str = Query("NEW", alias="fields[STATUS_ID]"),
    assigned_by_id: str = Query("510", alias="fields[ASSIGNED_BY_ID]"),
    uf_crm_1692256014: str = Query("IZAAP", alias="fields[UF_CRM_1692256014]"),
    uf_crm_1692255317548: str = Query("Developer", alias="fields[UF_CRM_1692255317548]"),
    uf_crm_1692256034: str = Query("9876543210", alias="fields[UF_CRM_1692256034]"),
    uf_crm_1692256050: str = Query("test@mail.com", alias="fields[UF_CRM_1692256050]"),
    uf_crm_64ddc37491441: str = Query("Demo_Product", alias="fields[UF_CRM_64DDC37491441]"),
    comments: str = Query("Test", alias="fields[COMMENTS]")
):
    """
    Test endpoint for Bitrix24 webhook to test using Swagger UI exactly like Postman.
    The parameters will be sent as form data to the Bitrix24 webhook URL.
    """
    webhook_url = settings.BITRIX24_WEBHOOK_URL
    if not webhook_url:
        raise HTTPException(
            status_code=500,
            detail="BITRIX24_WEBHOOK_URL is not configured on the server.",
        )

    bitrix_payload = {
        "fields[TITLE]": title,
        "fields[STATUS_ID]": status_id,
        "fields[ASSIGNED_BY_ID]": assigned_by_id,
        "fields[UF_CRM_1692256014]": uf_crm_1692256014,
        "fields[UF_CRM_1692255317548]": uf_crm_1692255317548,
        "fields[UF_CRM_1692256034]": uf_crm_1692256034,
        "fields[UF_CRM_1692256050]": uf_crm_1692256050,
        "fields[UF_CRM_64DDC37491441]": uf_crm_64ddc37491441,
        "fields[COMMENTS]": comments
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(webhook_url, data=bitrix_payload)

    bitrix_response = _parse_bitrix_response(response)
    if response.status_code != 200:
        return {
            "status": "partial_success",
            "message": "Test payload reached Bitrix24 but returned a non-200 response.",
            "bitrix_response": {
                "status": response.status_code,
                "body": bitrix_response,
            },
        }

    return {
        "status": "success",
        "message": "Test payload submitted to Bitrix24.",
        "bitrix_response": bitrix_response,
    }
