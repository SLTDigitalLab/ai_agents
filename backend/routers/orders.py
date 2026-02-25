"""
Orders router - receives LifeStore order submissions from the React
frontend and sends an email notification via fastapi-mail.

POST /api/v1/orders/submit
"""

from fastapi import APIRouter, BackgroundTasks
from fastapi_mail import FastMail, MessageSchema, MessageType

from core.config import get_mail_config
from schemas.order import OrderSubmission

router = APIRouter(prefix="/api/v1/orders", tags=["Lifestore"])


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
    </table>
    """

    # ── Configure the message ────────────────────────────────────────────
    message = MessageSchema(
        subject="New LifeStore Order",
        recipients=["dialogtv456@gmail.com"],
        body=html_body,
        subtype=MessageType.html,
    )

    # ── Send in the background so the API responds instantly ─────────────
    fm = FastMail(get_mail_config())
    background_tasks.add_task(fm.send_message, message)

    return {"status": "success", "message": "Order placed successfully"}
