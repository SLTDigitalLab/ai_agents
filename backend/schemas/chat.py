"""Pydantic request/response schemas for the chat endpoint."""

import re
from typing import Optional

from pydantic import BaseModel, Field, field_validator


MAX_CHAT_MESSAGE_CHARS = 1500
MAX_THREAD_ID_CHARS = 128
MAX_AGENT_ID_CHARS = 40
MAX_USER_ID_CHARS = 200
MAX_USER_NAME_CHARS = 200
MAX_DEPARTMENT_CHARS = 200
MAX_JOB_TITLE_CHARS = 200

CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")
SAFE_AGENT_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
SAFE_THREAD_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_.:-]+$")


class ChatRequest(BaseModel):
    """Incoming payload from the React frontend."""

    message: str = Field(
        ...,
        min_length=1,
        max_length=MAX_CHAT_MESSAGE_CHARS,
        description="User message. Limited to prevent spam/oversized prompt abuse.",
    )
    agent_id: str = Field(
        ...,
        min_length=1,
        max_length=MAX_AGENT_ID_CHARS,
        description="Target agent identifier.",
    )
    user_id: str = Field(
        ...,
        min_length=1,
        max_length=MAX_USER_ID_CHARS,
        description="Authenticated user identifier.",
    )
    user_name: Optional[str] = Field(
        default=None,
        max_length=MAX_USER_NAME_CHARS,
        description="Display name of the user, for admin attribution.",
    )
    department: Optional[str] = Field(
        default=None,
        max_length=MAX_DEPARTMENT_CHARS,
        description="User's department from Azure AD, for admin attribution.",
    )
    job_title: Optional[str] = Field(
        default=None,
        max_length=MAX_JOB_TITLE_CHARS,
        description="User's job title from Azure AD, for admin attribution.",
    )
    thread_id: Optional[str] = Field(
        default="default_thread",
        min_length=1,
        max_length=MAX_THREAD_ID_CHARS,
        description="Conversation thread identifier.",
    )

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        value = (value or "").strip()

        if not value:
            raise ValueError("Message cannot be empty.")

        if CONTROL_CHAR_PATTERN.search(value):
            raise ValueError("Message contains unsupported control characters.")

        return value

    @field_validator("agent_id")
    @classmethod
    def validate_agent_id(cls, value: str) -> str:
        value = (value or "").strip()

        if not SAFE_AGENT_ID_PATTERN.fullmatch(value):
            raise ValueError("Invalid agent_id format.")

        return value

    @field_validator("user_id")
    @classmethod
    def validate_user_id(cls, value: str) -> str:
        value = (value or "").strip()

        if CONTROL_CHAR_PATTERN.search(value):
            raise ValueError("Invalid user_id format.")

        return value

    @field_validator("user_name", mode="before")
    @classmethod
    def validate_user_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None

        value = str(value).strip()
        if not value:
            return None

        # Strip control characters; keep the rest of the display name intact.
        return CONTROL_CHAR_PATTERN.sub("", value)

    @field_validator("department", mode="before")
    @classmethod
    def validate_department(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None

        value = str(value).strip()
        if not value:
            return None

        # Strip control characters; keep the rest of the department name intact.
        return CONTROL_CHAR_PATTERN.sub("", value)

    @field_validator("job_title", mode="before")
    @classmethod
    def validate_job_title(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None

        value = str(value).strip()
        if not value:
            return None

        # Strip control characters; keep the rest of the title intact.
        return CONTROL_CHAR_PATTERN.sub("", value)

    @field_validator("thread_id", mode="before")
    @classmethod
    def validate_thread_id(cls, value: Optional[str]) -> str:
        if value is None:
            return "default_thread"

        value = str(value).strip() or "default_thread"

        if not SAFE_THREAD_ID_PATTERN.fullmatch(value):
            raise ValueError("Invalid thread_id format.")

        return value