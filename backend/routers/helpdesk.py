"""API routes for helpdesk tickets, solved tickets, and categories."""

import asyncio
import logging
import re
from typing import Literal, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from core.auth import get_verified_user
from core.admin_auth import admin_email, require_admin
from services import slt_crm_incidents as crm

from services.helpdesk_tickets import (
    # helpdesk tickets
    create_helpdesk_ticket,
    get_helpdesk_ticket,
    list_helpdesk_tickets,
    # solved tickets
    create_solved_ticket,
    get_solved_ticket,
    list_solved_tickets,
    # categories
    create_category,
    get_category,
    list_categories,
)

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/helpdesk_dev", tags=["Helpdesk"],
    dependencies=[Depends(get_verified_user)],
)

# SLT logins are `<employeeNumber>@intranet.slt.com.lk`; the CRM's
# SLTAPIUserID is that employee number (e.g. "016242").
_SLT_LOGIN_DOMAIN = "intranet.slt.com.lk"
_EMPLOYEE_NUMBER_RE = re.compile(r"^[a-z0-9]{1,20}$")


def _owner_id(user: dict) -> str:
    # Existing tickets use Microsoft usernames. Never take ownership from input.
    owner = admin_email(user)
    if not user.get("oid") or not owner:
        raise HTTPException(status_code=403, detail="Helpdesk user identity is required.")
    return owner


async def _ticket_user(user: dict, requested: Optional[str]) -> str:
    owner = _owner_id(user)
    if requested is None:
        return owner
    if not isinstance(requested, str) or not requested.strip():
        raise HTTPException(status_code=422, detail="userId must be a nonempty string.")
    target = requested.strip().lower()
    if target != owner:
        await require_admin(user)
    return target


# ---------------------------------------------------------------------------
# Helpdesk tickets
# ---------------------------------------------------------------------------

@router.get("/tickets")
async def get_tickets(
    userId: Optional[str] = Query(None, description="Filter tickets by userId"),
    status: Optional[str] = Query(None, description="Filter tickets by status"),
    limit: int = Query(100, ge=1, le=500),
    user: dict = Depends(get_verified_user),
):
    """List own tickets; administrators may explicitly select another user."""
    owner = await _ticket_user(user, userId)
    tickets = list_helpdesk_tickets(user_id=owner, status=status, limit=limit)
    return {"tickets": tickets, "count": len(tickets)}


@router.get("/tickets/{ticket_id}")
async def get_ticket(ticket_id: str, user: dict = Depends(get_verified_user)):
    """Fetch a single helpdesk ticket by ticket_id."""
    owner = _owner_id(user)
    ticket = get_helpdesk_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail=f"Ticket '{ticket_id}' not found")
    if str(ticket.get("userId") or "").strip().lower() != owner:
        try:
            await require_admin(user)
        except HTTPException as exc:
            if exc.status_code != 403:
                raise
            # Do not reveal whether another user's ticket exists.
            raise HTTPException(status_code=404, detail=f"Ticket '{ticket_id}' not found")
    return ticket


@router.post("/tickets")
async def post_ticket(body: dict, user: dict = Depends(get_verified_user)):
    """Create a new helpdesk ticket.
    Expected body fields:
        userId              (str, optional; defaults to verified user)
        message             (str, required)
        sub_category        (str, required)
        ticket_id           (str, optional)
        status              (str, optional)
        main_category       (str, optional)
        duplicate_check     (str, optional, default 'open')
        need_more_informations  (str, optional, default 'normal')
        updated_main_category   (str, optional, default 'chat')
        updated_sub_category    (str, optional)
    """
    owner = await _ticket_user(user, body.get("userId"))
    # Workflow state and ticket identifiers may only be supplied by admins.
    managed_fields = {
        "ticket_id", "status", "duplicate_check", "need_more_informations",
        "updated_main_category", "updated_sub_category",
    }
    if managed_fields.intersection(body):
        await require_admin(user)
    required = ("message", "sub_category")
    missing = [f for f in required if not body.get(f)]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Missing required fields: {', '.join(missing)}",
        )

    ticket = create_helpdesk_ticket(
        user_id=owner,
        message=body["message"],
        sub_category=body["sub_category"],
        ticket_id=body.get("ticket_id"),
        status=body.get("status"),
        main_category=body.get("main_category"),
        duplicate_check=body.get("duplicate_check", "open"),
        need_more_informations=body.get("need_more_informations", "normal"),
        updated_main_category=body.get("updated_main_category", "chat"),
        updated_sub_category=body.get("updated_sub_category"),
    )
    return ticket


# ---------------------------------------------------------------------------
# CRM incidents (helpdesk ticket form)
# ---------------------------------------------------------------------------

class IncidentForm(BaseModel):
    # 255 = helpdesk_tickets.message column size (local copy for duplicate check).
    description: str = Field(..., max_length=255)
    incident_type: str = Field(..., max_length=100)
    incident_sub_type: str = Field(..., max_length=100)
    priority: Literal["Low", "Medium", "High"]

    @field_validator("description", "incident_type", "incident_sub_type")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value


def _employee_number(owner: str) -> str:
    local, _, domain = owner.partition("@")
    if domain != _SLT_LOGIN_DOMAIN or not _EMPLOYEE_NUMBER_RE.match(local):
        raise HTTPException(
            status_code=403,
            detail="Tickets can only be raised from an SLT employee account.",
        )
    return local


@router.post("/incidents")
async def post_incident(form: IncidentForm, user: dict = Depends(get_verified_user)):
    """Create an incident in SLT's CRM from the helpdesk ticket form, then keep
    a local copy so the chat's duplicate check can see it.

    The submitter is always the verified login; nothing about identity is
    read from the body.
    """
    owner = _owner_id(user)
    employee_number = _employee_number(owner)

    try:
        created = await crm.create_incident(
            description=form.description,
            incident_type=form.incident_type,
            incident_sub_type=form.incident_sub_type,
            priority=form.priority,
            sltapi_user_id=employee_number,
        )
    except crm.CrmNotConfiguredError:
        log.error("post_incident: CRM create endpoint not configured")
        raise HTTPException(status_code=503, detail="Ticket creation is not available right now.")
    except httpx.TimeoutException:
        log.warning("post_incident: CRM timed out | user=%s", employee_number)
        raise HTTPException(
            status_code=504,
            # The CRM may still have created the incident, so don't invite
            # an immediate retry (that could create a duplicate).
            detail=(
                "The ticket system is taking longer than usual to respond. Your ticket "
                "may still have been created, so please don't submit again right away. "
                "Check with the Help Desk or try again in a few minutes."
            ),
        )
    except (crm.CrmIncidentError, httpx.HTTPError) as exc:
        log.error("post_incident: CRM failed | user=%s | %s", employee_number, exc)
        raise HTTPException(
            status_code=502,
            detail="The ticket system could not create your ticket. Please try again later.",
        )

    # The incident already exists in the CRM, so a local save failure must not
    # turn into an error for the user (a retry would create a second incident).
    try:
        await asyncio.to_thread(
            create_helpdesk_ticket,
            user_id=owner,
            message=form.description,
            ticket_id=created.incident_id,
            status="open",
            main_category=form.incident_type,
            sub_category=form.incident_sub_type,
        )
    except Exception as exc:
        log.error(
            "post_incident: CRM incident %s created but local save failed: %s",
            created.incident_id, exc,
        )

    return {
        "incident_id": created.incident_id,
        "description": form.description,
        "incident_type": form.incident_type,
        "incident_sub_type": form.incident_sub_type,
        "priority": form.priority,
        "status": "Assigned",
    }


# ---------------------------------------------------------------------------
# Solved tickets
# ---------------------------------------------------------------------------

@router.get("/solved-tickets", dependencies=[Depends(require_admin)])
async def get_solved_tickets(
    limit: int = Query(100, ge=1, le=500),
):
    """List solved tickets. Used by the LangGraph agent as a knowledge base
    to match incoming issues against previously resolved ones.
    """
    tickets = list_solved_tickets(limit=limit)
    return {"solved_tickets": tickets, "count": len(tickets)}


@router.get("/solved-tickets/{solved_id}", dependencies=[Depends(require_admin)])
async def get_solved_ticket_by_id(solved_id: int):
    """Fetch a single solved ticket by its numeric id."""
    ticket = get_solved_ticket(solved_id)
    if not ticket:
        raise HTTPException(
            status_code=404, detail=f"Solved ticket '{solved_id}' not found"
        )
    return ticket


@router.post("/solved-tickets", dependencies=[Depends(require_admin)])
async def post_solved_ticket(body: dict):
    """Store a newly solved ticket.
    Expected body fields:
        requirements    (str, required)  — the original problem / requirements
        answer          (str, required)  — the resolution
    """
    required = ("requirements", "answer")
    missing = [f for f in required if not body.get(f)]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Missing required fields: {', '.join(missing)}",
        )

    ticket = create_solved_ticket(
        requirements=body["requirements"],
        answer=body["answer"],
    )
    return ticket


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------

@router.get("/categories", dependencies=[Depends(require_admin)])
async def get_categories(
    category_name: Optional[str] = Query(
        None, description="Filter by category name"
    ),
    limit: int = Query(100, ge=1, le=500),
):
    """List all categories and their subcategories.
    Used by the LangGraph agent to classify and route incoming tickets.
    """
    cats = list_categories(category_name=category_name, limit=limit)
    return {"categories": cats, "count": len(cats)}


@router.get("/categories/{category_id}", dependencies=[Depends(require_admin)])
async def get_category_by_id(category_id: str):
    """Fetch a single category by its category_id."""
    cat = get_category(category_id)
    if not cat:
        raise HTTPException(
            status_code=404, detail=f"Category '{category_id}' not found"
        )
    return cat


@router.post("/categories", dependencies=[Depends(require_admin)])
async def post_category(body: dict):
    """Create a new category entry.
    Expected body fields:
        category_id     (str, required)
        category_name   (str, required)
        subcategory     (str, required)
        description     (str, optional)
        keywords        (str, optional)
        example_tickets (str, optional)
    """
    required = ("category_id", "category_name", "subcategory")
    missing = [f for f in required if not body.get(f)]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Missing required fields: {', '.join(missing)}",
        )

    cat = create_category(
        category_id=body["category_id"],
        category_name=body["category_name"],
        subcategory=body["subcategory"],
        description=body.get("description"),
        keywords=body.get("keywords"),
        example_tickets=body.get("example_tickets"),
    )
    return cat
