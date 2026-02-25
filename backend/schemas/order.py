"""Pydantic request/response schemas for the order endpoint."""

from typing import Optional

from pydantic import BaseModel


class OrderSubmission(BaseModel):
    """Incoming payload from the LifeStore order form."""

    product: Optional[str] = None
    fullName: str
    deliveryAddress: str
    phone: str
