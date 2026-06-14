"""Pydantic request/response schemas for the chat endpoint."""

from typing import Optional

from pydantic import BaseModel


class ChatRequest(BaseModel):
    """Incoming payload from the React frontend."""

    message: str
    agent_id: str
    user_id: str
    user_name: Optional[str] = None
    thread_id: Optional[str] = "default_thread"
