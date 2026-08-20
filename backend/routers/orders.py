"""
Orders router - receives LifeStore order submissions from the React
frontend and sends an email notification via fastapi-mail.

POST /api/v1/orders/submit
"""

from fastapi import APIRouter, BackgroundTasks
from fastapi_mail import FastMail, MessageSchema, MessageType

from core.config import get_mail_config
from services.bizleads import submit_bizlead
from schemas.order import OrderSubmission

router = APIRouter(prefix="/api/v1/orders", tags=["Lifestore"])


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
async def submit_order(order: OrderSubmission, background_tasks: BackgroundTasks):
    """Accept an order and queue an email notification in the background."""

    # ── Build a clean HTML email body ────────────────────────────────────
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
        <tr>
            <th style="background:#f2f2f2; text-align:left;">Email</th>
            <td>{order.email or "N/A"}</td>
        </tr>
        <tr>
            <th style="background:#f2f2f2; text-align:left;">City</th>
            <td>{order.city or "N/A"}</td>
        </tr>
        <tr>
            <th style="background:#f2f2f2; text-align:left;">Note</th>
            <td>{order.note or "N/A"}</td>
        </tr>
    </table>
    """

    # ── Configure the message ────────────────────────────────────────────
    message = MessageSchema(
        subject="New LifeStore Order",
        recipients=["lifestore@slt.lk"],
        body=html_body,
        subtype=MessageType.html,
    )

    # ── Send in the background so the API responds instantly ─────────────
    fm = FastMail(get_mail_config())
    background_tasks.add_task(fm.send_message, message)

    if _has_bizleads_fields(order):
        background_tasks.add_task(
            submit_bizlead,
            name=order.fullName,
            phone=order.phone,
            email=order.email,
            city=order.city,
            product=order.product or "LifeStore Order",
            note=order.note or order.deliveryAddress,
        )

    return {"status": "success", "message": "Order placed successfully"}
