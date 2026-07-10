"""
realtime.py — Live Voice Agent router for Workmate AI

Provider switching — edit .env only, no code changes needed:

  USE GEMINI (Vertex AI):
    GOOGLE_APPLICATION_CREDENTIALS=service-account.json
    PROJECT_ID=visionflow-ai-495406
    LOCATION=us-central1

  USE OPENAI:
    OPENAI_API_KEY=your_key
    # comment out GOOGLE_APPLICATION_CREDENTIALS

Endpoints:
  GET  /api/v1/realtime/provider        — which provider is active
  GET  /api/v1/realtime/token           — OpenAI ephemeral token (WebRTC)
  POST /api/v1/realtime/session-token   — exchange MSAL token for session token
  POST /api/v1/realtime/leave-balance   — leave balance lookup (OpenAI path)
  WS   /api/v1/realtime/ws/voice        — Gemini Live proxy (WebSocket)
  POST /api/v1/realtime/rag-search      — RAG search (both providers)
"""

import logging

import httpx
from fastapi import APIRouter, HTTPException

from core.config import settings
from .tools import GEMINI_LIVE_MODEL, OPENAI_REALTIME_MODEL
from .session import SessionTokenRequest, LeaveBalanceRequest, create_voice_session, get_leave_balance_endpoint
from .rag_search import RAGSearchRequest, RAGSearchResponse, rag_search
from .gemini_proxy import gemini_voice_proxy

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/realtime", tags=["realtime"])


def _active_provider() -> str:
    if settings.GOOGLE_APPLICATION_CREDENTIALS:
        return "gemini"
    if settings.OPENAI_API_KEY:
        return "openai"
    return "none"


@router.get("/provider")
async def get_voice_provider():
    provider = _active_provider()
    if provider == "none":
        raise HTTPException(
            status_code=500,
            detail="No voice provider configured. Set GOOGLE_APPLICATION_CREDENTIALS or OPENAI_API_KEY in .env."
        )
    model = GEMINI_LIVE_MODEL if provider == "gemini" else OPENAI_REALTIME_MODEL
    logger.info(f"Voice provider: {provider} / {model}")
    return {"provider": provider, "model": model}


@router.get("/token")
async def get_realtime_token():
    if not settings.OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY is not configured.")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                "https://api.openai.com/v1/realtime/client_secrets",
                headers={
                    "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                    "OpenAI-Safety-Identifier": "workmate-ai-voice",
                },
                json={
                    "session": {
                        "type": "realtime",
                        "model": OPENAI_REALTIME_MODEL,
                        "audio": {"output": {"voice": "alloy"}},
                    }
                },
            )

        if response.status_code != 200:
            logger.error(f"OpenAI token error: {response.status_code} — {response.text}")
            raise HTTPException(status_code=502, detail=f"Failed to create OpenAI session: {response.text}")

        data = response.json()
        logger.info(f"OpenAI ephemeral token generated — keys: {list(data.keys())}")
        return data

    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Timeout connecting to OpenAI")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"OpenAI token error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/session-token")
async def _create_voice_session(request: SessionTokenRequest):
    return await create_voice_session(request)


@router.post("/leave-balance")
async def _get_leave_balance(request: LeaveBalanceRequest):
    return await get_leave_balance_endpoint(request)


@router.websocket("/ws/voice")
async def _gemini_voice_proxy(websocket):
    await gemini_voice_proxy(websocket)


@router.post("/rag-search", response_model=RAGSearchResponse)
async def _rag_search(request: RAGSearchRequest):
    return await rag_search(request)