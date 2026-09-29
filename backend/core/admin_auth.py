"""Server-side admin authorization. Never trust identity from request bodies."""

import json
import os

from fastapi import Depends, HTTPException

from core.auth import get_verified_user


def admin_email(user: dict) -> str:
    # Use one authoritative claim; do not try alternate claims to find a match.
    return str(user.get("preferred_username") or user.get("email") or "").strip().lower()


async def require_admin(user: dict = Depends(get_verified_user)) -> dict:
    # Existing installations keep working with the server's existing .env.
    # An explicitly empty ADMIN_EMAILS denies everyone (no fallback).
    configured = os.getenv("ADMIN_EMAILS", os.getenv("VITE_ADMIN_EMAILS", ""))
    allowed = {email.strip().lower() for email in configured.split(",") if email.strip()}
    if not user.get("oid") or admin_email(user) not in allowed:
        raise HTTPException(status_code=403, detail="Administrator access required.")
    return user


def require_agent_access(user: dict, agent_name: str | None) -> None:
    """Check the verified administrator's permission for the actual target agent."""
    try:
        mapping = json.loads(os.getenv("ADMIN_AGENT_MAP", "{}"))
        if not isinstance(mapping, dict):
            raise ValueError("Expected an object")
        allowed = next(
            (agents for email, agents in mapping.items()
             if email.strip().lower() == admin_email(user)),
            [],
        )
        if not isinstance(allowed, list) or not all(isinstance(a, str) for a in allowed):
            raise ValueError("Expected an array of agent IDs")
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=403, detail="Admin agent permissions are not configured correctly.")
    if agent_name and ("*" in allowed or agent_name in allowed):
        return
    raise HTTPException(status_code=403, detail="Administrator cannot access this agent's knowledge base.")
