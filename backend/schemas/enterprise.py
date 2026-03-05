"""Pydantic request/response schemas for the enterprise lead endpoint."""

from typing import Optional

from pydantic import BaseModel


class EnterpriseLead(BaseModel):
    """Incoming payload from the Enterprise Service Request form."""

    company_name: str
    business_registration_number: Optional[str] = None
    contact_person: str
    contact_number: str
    email: str
    select_service: str
    remarks: Optional[str] = None
