"""Pydantic request/response schemas for the enterprise lead endpoint."""

import re
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, field_validator

from schemas.locations import is_known_city

NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z .'-]*$")
CITY_PATTERN = re.compile(r"^[A-Za-z][A-Za-z .'-]*$")
PHONE_PATTERN = re.compile(r"^\+?\d{9,15}$")


class EnterpriseLead(BaseModel):
    """Incoming payload from the Enterprise Service Request form."""

    company_name: str = Field(..., min_length=2, max_length=200)
    business_registration_number: Optional[str] = None
    contact_person: str = Field(..., min_length=2, max_length=100)
    contact_number: str
    email: EmailStr
    city: Optional[str] = Field(None, min_length=3, max_length=50)
    select_service: str
    remarks: Optional[str] = None
    note: Optional[str] = None

    @field_validator("contact_person")
    @classmethod
    def validate_contact_person(cls, value: str) -> str:
        if not NAME_PATTERN.match(value.strip()):
            raise ValueError("Contact person must contain only letters and spaces")
        return value.strip()

    @field_validator("city")
    @classmethod
    def validate_city(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        cleaned = value.strip()
        if not CITY_PATTERN.match(cleaned):
            raise ValueError("City must contain only letters and spaces")
        if not is_known_city(cleaned):
            raise ValueError("City must be a recognized Sri Lankan city/town")
        return cleaned

    @field_validator("contact_number")
    @classmethod
    def validate_contact_number(cls, value: str) -> str:
        cleaned = re.sub(r"[\s()-]", "", value)
        if not PHONE_PATTERN.match(cleaned):
            raise ValueError("Contact number must contain 9-15 digits")
        return cleaned
