"""Bind chat storage and tool identity to authenticated callers.

Never look up legacy checkpoints using raw client thread IDs: their recorded
user identity was supplied by the client and cannot establish ownership.
"""
import hashlib
import json
import re
from dataclasses import dataclass

from fastapi import HTTPException

PUBLIC_AGENT_IDS = frozenset({"aiexpo", "backoffice_email", "embryo", "enterprise", "lifestore", "rainbowpages"})


def require_agent_identity(agent_id: str, user: dict | None) -> None:
    if agent_id not in PUBLIC_AGENT_IDS and user is None:
        raise HTTPException(status_code=401, detail="Not authenticated",
                            headers={"WWW-Authenticate": "Bearer"})


@dataclass(frozen=True)
class ChatIdentity:
    principal: str
    user_id: str
    user_name: str | None = None
    department: str | None = None
    job_title: str | None = None


def chat_identity(user: dict | None, guest_session: str | None) -> ChatIdentity:
    if user is not None:
        tenant = str(user.get("tid") or "").strip()
        oid = str(user.get("oid") or "").strip()
        email = str(user.get("preferred_username") or user.get("email") or "").strip().lower()
        if not tenant or not oid or not email:
            raise HTTPException(status_code=403, detail="Verified chat identity is required.")
        return ChatIdentity(
            principal=json.dumps(["microsoft", tenant, oid], separators=(",", ":")),
            user_id=email,
            user_name=user.get("name") or email,
            department=user.get("department"),
            job_title=user.get("jobTitle"),
        )
    # A separate high-entropy credential is required; a thread ID is not a secret.
    if not guest_session or not re.fullmatch(r"[0-9a-f]{64}", guest_session):
        raise HTTPException(status_code=401, detail="A guest chat session is required.")
    digest = hashlib.sha256(guest_session.encode()).hexdigest()
    return ChatIdentity(principal="guest:" + digest, user_id="guest:" + digest,
                        user_name="Guest")


def scoped_thread_id(agent_id: str, client_thread_id: str, identity: ChatIdentity) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,40}", agent_id):
        raise HTTPException(status_code=422, detail="Invalid agent ID.")
    if not client_thread_id or not re.fullmatch(r"[a-zA-Z0-9_.:-]{1,128}", client_thread_id):
        raise HTTPException(status_code=422, detail="Invalid thread ID.")
    payload = json.dumps([agent_id, identity.principal, client_thread_id], separators=(",", ":"))
    return "chat_v2_" + hashlib.sha256(payload.encode()).hexdigest()


def bind_chat_request(request, identity: ChatIdentity):
    """Overwrite every caller-selected identity field before any tool runs."""
    return request.model_copy(update={
        "thread_id": scoped_thread_id(request.agent_id, request.thread_id, identity),
        "user_id": identity.user_id,
        "user_name": identity.user_name,
        "department": identity.department,
        "job_title": identity.job_title,
    })
