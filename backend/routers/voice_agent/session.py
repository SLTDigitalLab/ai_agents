# session token + leave balance ( session.py)

import asyncio
import logging
import secrets
import time

import httpx
from fastapi import HTTPException
from pydantic import BaseModel

from domain.tools.api_tools import fetch_leave_balance_for_user

logger = logging.getLogger(__name__)

_voice_sessions: dict[str, dict] = {}
_SESSION_TTL = 300

#session management functions for get endpoint, create session endpoint, and leave balance endpoint
def _store_session(email: str) -> str:
    token = secrets.token_urlsafe(32)
    _voice_sessions[token] = {"email": email, "expires_at": time.time() + _SESSION_TTL}
    expired = [k for k, v in _voice_sessions.items() if v["expires_at"] < time.time()]
    for k in expired:
        del _voice_sessions[k]
    return token


def _resolve_session(token: str) -> str | None:
    entry = _voice_sessions.get(token)
    if not entry:
        return None
    if entry["expires_at"] < time.time():
        del _voice_sessions[token]
        return None
    return entry["email"]


class SessionTokenRequest(BaseModel):
    msal_token: str


class LeaveBalanceRequest(BaseModel):
    session_token: str


async def create_voice_session(request: SessionTokenRequest):
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            graph_res = await client.get(
                "https://graph.microsoft.com/v1.0/me",
                headers={"Authorization": f"Bearer {request.msal_token}"},
            )

        if graph_res.status_code != 200:
            raise HTTPException(status_code=401, detail="Invalid or expired MSAL token")

        profile = graph_res.json()
        email = profile.get("userPrincipalName") or profile.get("mail") or ""
        if not email:
            raise HTTPException(status_code=400, detail="Could not retrieve email from Microsoft account")

        session_token = _store_session(email)
        logger.info(f"Voice session created for user: {email[:4]}...@...")
        return {"session_token": session_token}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Session token error: {e}")
        raise HTTPException(status_code=500, detail="Failed to verify identity")


async def get_leave_balance_endpoint(request: LeaveBalanceRequest):
    email = _resolve_session(request.session_token)
    if not email:
        return {"result": "Your session has expired. Please end the call and start a new conversation to check your leave balance."}
    try:
        result = await asyncio.to_thread(fetch_leave_balance_for_user, email)
        return {"result": result}
    except Exception as e:
        logger.error(f"Leave balance endpoint error: {e}")
        return {"result": "Unable to fetch leave balance at the moment."}