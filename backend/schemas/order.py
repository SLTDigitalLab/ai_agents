"""Pydantic request/response schemas for the order endpoint."""

import re
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from schemas.locations import is_known_city
from services.lifestore_catalog import catalog_available, get_product_by_id

NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z .'-]*$")
CITY_PATTERN = re.compile(r"^[A-Za-z][A-Za-z .'-]*$")
PHONE_PATTERN = re.compile(r"^\+?\d{9,15}$")


class OrderSubmission(BaseModel):
    """Incoming payload from the LifeStore order form."""

    product: Optional[str] = None
    product_id: Optional[str] = None
    fullName: str = Field(..., min_length=2, max_length=100)
    deliveryAddress: str = Field(..., min_length=5, max_length=200)
    phone: str
    email: Optional[EmailStr] = None
    city: Optional[str] = Field(None, min_length=3, max_length=50)
    note: Optional[str] = None

    @field_validator("fullName")
    @classmethod
    def validate_full_name(cls, value: str) -> str:
        if not NAME_PATTERN.match(value.strip()):
            raise ValueError("Full name must contain only letters and spaces")
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

    @model_validator(mode="after")
    def validate_product(self) -> "OrderSubmission":
        if self.product_id:
            catalog_product = get_product_by_id(self.product_id)
            if not catalog_product:
                raise ValueError("Selected product was not found in the catalog")
            self.product = catalog_product["name"]
        elif catalog_available():
        # No product_id supplied and the catalog is loaded — reject regardless
        # of whether free text was typed, since a product selection is required.
            raise ValueError("Please select a product from the search suggestions")
        return self

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        cleaned = re.sub(r"[\s()-]", "", value)
        if not PHONE_PATTERN.match(cleaned):
            raise ValueError("Phone number must contain 9-15 digits")
        return cleaned
