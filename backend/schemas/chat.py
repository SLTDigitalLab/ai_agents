"""Pydantic request/response schemas for the chat endpoint."""

from typing import Optional

from pydantic import BaseModel


class ChatRequest(BaseModel):
    """Incoming payload from the React frontend."""

    message: str
    agent_id: str
    user_id: str
    thread_id: Optional[str] = "default_thread"
    # Set false to force a fresh LLM path (e.g. debugging).
    use_cache: bool = True
