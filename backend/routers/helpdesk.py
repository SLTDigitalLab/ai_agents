"""API routes for helpdesk tickets, solved tickets, and categories."""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

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

router = APIRouter(prefix="/api/v1/helpdesk_dev", tags=["Helpdesk"])


# ---------------------------------------------------------------------------
# Helpdesk tickets
# ---------------------------------------------------------------------------

@router.get("/tickets")
async def get_tickets(
    userId: Optional[str] = Query(None, description="Filter tickets by userId"),
    status: Optional[str] = Query(None, description="Filter tickets by status"),
    limit: int = Query(100, ge=1, le=500),
):
    """List recent helpdesk tickets, optionally filtered by userId or status.
    Used by the LangGraph agent to fetch a user's ticket history.
    """
    tickets = list_helpdesk_tickets(user_id=userId, status=status, limit=limit)
    return {"tickets": tickets, "count": len(tickets)}


@router.get("/tickets/{ticket_id}")
async def get_ticket(ticket_id: str):
    """Fetch a single helpdesk ticket by ticket_id."""
    ticket = get_helpdesk_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail=f"Ticket '{ticket_id}' not found")
    return ticket


@router.post("/tickets")
async def post_ticket(body: dict):
    """Create a new helpdesk ticket.
    Expected body fields:
        userId              (str, required)
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
    required = ("userId", "message", "sub_category")
    missing = [f for f in required if not body.get(f)]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Missing required fields: {', '.join(missing)}",
        )

    ticket = create_helpdesk_ticket(
        user_id=body["userId"],
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
# Solved tickets
# ---------------------------------------------------------------------------

@router.get("/solved-tickets")
async def get_solved_tickets(
    limit: int = Query(100, ge=1, le=500),
):
    """List solved tickets. Used by the LangGraph agent as a knowledge base
    to match incoming issues against previously resolved ones.
    """
    tickets = list_solved_tickets(limit=limit)
    return {"solved_tickets": tickets, "count": len(tickets)}


@router.get("/solved-tickets/{solved_id}")
async def get_solved_ticket_by_id(solved_id: int):
    """Fetch a single solved ticket by its numeric id."""
    ticket = get_solved_ticket(solved_id)
    if not ticket:
        raise HTTPException(
            status_code=404, detail=f"Solved ticket '{solved_id}' not found"
        )
    return ticket


@router.post("/solved-tickets")
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

@router.get("/categories")
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


@router.get("/categories/{category_id}")
async def get_category_by_id(category_id: str):
    """Fetch a single category by its category_id."""
    cat = get_category(category_id)
    if not cat:
        raise HTTPException(
            status_code=404, detail=f"Category '{category_id}' not found"
        )
    return cat


@router.post("/categories")
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