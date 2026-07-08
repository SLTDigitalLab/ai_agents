import os
import smtplib
from email.message import EmailMessage

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr, Field
from email.utils import formataddr


router = APIRouter(prefix="/api/v1", tags=["contact"])


class ContactRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    email: EmailStr
    title: str = Field(..., min_length=1, max_length=200)
    message: str = Field(..., min_length=1, max_length=5000)


@router.post("/contact-us")
def send_contact_message(request: ContactRequest):
    receiver_email = os.getenv("CONTACT_RECEIVER_EMAIL")
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_username = os.getenv("SMTP_USERNAME")
    smtp_password = os.getenv("SMTP_PASSWORD")
    smtp_from_email = os.getenv("SMTP_FROM_EMAIL", smtp_username)
    smtp_from_name = os.getenv("SMTP_FROM_NAME", "Workmate AI")

    if not all([receiver_email, smtp_host, smtp_username, smtp_password, smtp_from_email]):
        raise HTTPException(
            status_code=500,
            detail="Contact email service is not configured properly."
        )

    email_body = f"""
New Contact Us Message

Name:
{request.name}

Email:
{request.email}

Title:
{request.title}

Message:
{request.message}
"""

    msg = EmailMessage()
    msg["Subject"] = f"Contact Us: {request.title}"
    msg["From"] = formataddr((smtp_from_name, smtp_from_email))
    msg["To"] = receiver_email
    msg["Reply-To"] = request.email
    msg.set_content(email_body)

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.send_message(msg)

        return {
            "status": "success",
            "message": "Your message has been sent successfully."
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to send contact message: {str(e)}"
        )