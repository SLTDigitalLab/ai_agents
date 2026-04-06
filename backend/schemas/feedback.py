"""Pydantic request/response schemas for the feedback endpoint."""

from typing import Optional, Literal

from pydantic import BaseModel


class FeedbackRequest(BaseModel):
    """Submit or update feedback for a specific AI message."""

    agent_id: str
    thread_id: str
    message_index: int  # index of the AI message in the conversation
    rating: Literal["up", "down"]
    user_id: str


class FeedbackResponse(BaseModel):
    """Response after submitting feedback."""

    id: int
    agent_id: str
    thread_id: str
    message_index: int
    rating: str
    user_id: str
