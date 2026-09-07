"""
Orders router - receives LifeStore order submissions from the React
frontend, sends an email notification, and syncs the lead to BizLeads.

POST /api/v1/orders/submit
"""

import logging

from fastapi import APIRouter, HTTPException
from fastapi_mail import FastMail, MessageSchema, MessageType

from core.config import get_mail_config
from services.bizleads import submit_bizlead
from services.lifestore_catalog import search_products
from schemas.order import OrderSubmission

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/orders", tags=["Lifestore"])


@router.get("/products/search")
async def search_lifestore_products(q: str = ""):
    """Autocomplete search over the real LifeStore product catalog."""
    matches = search_products(q, limit=10)
    return {
        "results": [
            {"product_id": p["product_id"], "name": p["name"], "price": p.get("price")}
            for p in matches
        ]
    }


def _has_bizleads_fields(order: OrderSubmission) -> bool:
    return all(
        [
            order.fullName.strip(),
            order.deliveryAddress.strip(),
            order.phone.strip(),
            order.email.strip() if order.email else "",
            order.city.strip() if order.city else "",
        ]
    )


@router.post("/submit")
async def submit_order(order: OrderSubmission):
    """Accept an order. Both the email notification and the BizLeads sync
    must succeed for the order to be reported as successful."""

    html_body = f"""
    <h2>New LifeStore Order</h2>
    <table border="1" cellpadding="8" cellspacing="0"
           style="border-collapse: collapse; font-family: Arial, sans-serif;">
        <tr>
            <th style="background:#f2f2f2; text-align:left;">Product</th>
            <td>{order.product or "N/A"}</td>
        </tr>
        <tr>
            <th style="background:#f2f2f2; text-align:left;">Full Name</th>
            <td>{order.fullName}</td>
        </tr>
        <tr>
            <th style="background:#f2f2f2; text-align:left;">Delivery Address</th>
            <td>{order.deliveryAddress}</td>
        </tr>
        <tr>
            <th style="background:#f2f2f2; text-align:left;">Phone</th>
            <td>{order.phone}</td>
        </tr>
    </table>
    """

    message = MessageSchema(
        subject="New LifeStore Order",
        recipients=["lifestore@slt.lk"],
        body=html_body,
        subtype=MessageType.html,
    )

    # ── Email — the primary way the team is notified. Must succeed.
    fm = FastMail(get_mail_config())
    try:
        await fm.send_message(message)
    except Exception:
        logger.exception(
            "Failed to send LifeStore order notification email for phone=%s",
            order.phone,
        )
        raise HTTPException(
            status_code=502,
            detail="We couldn't confirm your order right now. Please try again, or contact us directly.",
        )

    # ── BizLeads — now critical, with a message tailored to the failure type.
    if _has_bizleads_fields(order):
        result = await submit_bizlead(
            name=order.fullName,
            phone=order.phone,
            email=order.email,
            city=order.city,
            product=order.product or "LifeStore Order",
            note=order.note or order.deliveryAddress,
        )

        if result == "network_error":
            logger.error("BizLeads network error after email succeeded for phone=%s", order.phone)
            raise HTTPException(
                status_code=503,
                detail="We're having trouble reaching our systems right now. Please try again in a moment.",
            )

        if result == "server_error":
            logger.error("BizLeads server error after email succeeded for phone=%s", order.phone)
            raise HTTPException(
                status_code=502,
                detail="Your order notification was sent, but we couldn't complete the full submission. Please contact us to confirm your order.",
            )

        # "success", "duplicate", and "skipped" are all fine — order proceeds.

    return {"status": "success", "message": "Order placed successfully"}