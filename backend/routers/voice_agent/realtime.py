"""
realtime.py — Live Voice Agent router for Workmate AI
Provides two endpoints:

  GET  /api/v1/realtime/token
       Generates a short-lived ephemeral token so the browser can open a
       WebRTC session directly with the OpenAI Realtime API.
       The real OPENAI_API_KEY never leaves this server.

  POST /api/v1/realtime/rag-search
       Called by OpenAI function-calling during a live voice session.
       Receives the user's spoken query, searches the existing Qdrant
       knowledge base, and returns relevant document chunks back to
       the Realtime API so it can speak an accurate answer.

No changes are made to any existing router, agent, or database.
"""

import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import httpx

from core.config import settings
from domain.tools.rag_tools import _search_qdrant_knowledge_base

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/realtime", tags=["realtime"])

#  System prompt injected into every live voice session 
# Mirrors the supervisor agent persona but adapted for spoken conversation.
VOICE_SYSTEM_PROMPT = """You are Workmate AI, the intelligent voice assistant for SLTMobitel employees.
You help employees with questions about HR policies, Finance, IT support, Admin procedures, 
internal audit (CIA), and business processes.

You are having a live voice conversation. Keep your responses:
- Concise and clear — this is a spoken conversation, not a chat interface
- Natural sounding — avoid bullet points or markdown formatting
- Accurate — always use the search_knowledge_base function when answering 
  questions about company policies, procedures, leave, benefits, or any 
  SLTMobitel-specific information

When you don't have enough information, use search_knowledge_base before answering.
If a question is completely outside SLTMobitel workplace topics, politely say you 
can only help with work-related questions.

Always greet the user warmly at the start of the conversation."""

#  Schemas 

class RAGSearchRequest(BaseModel):
    query: str
    agent_id: str = "supervisor"   # which Qdrant collection to search
    top_k: int = 5


class RAGSearchResponse(BaseModel):
    results: list[dict]


# Endpoint 01, Generate ephemeral session token for browser to connect to OpenAI Realtime API

@router.get("/token")
async def get_realtime_token():
    """
    Calls the OpenAI Realtime sessions API using the server-side OPENAI_API_KEY
    and returns a short-lived ephemeral token to the browser.

    The browser uses this token to open a WebRTC connection directly to OpenAI.
    The token expires in 60 seconds , enough time to establish the WebRTC session.
    """
    if not settings.OPENAI_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="OPENAI_API_KEY is not configured on the server."
        )

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
        "model": "gpt-realtime-2",
        "audio": {
            "output": {
                "voice": "alloy",
            }
        },
    }
},

           )

        if response.status_code != 200:
            logger.error(f"OpenAI Realtime session error: {response.status_code} — {response.text}")
            raise HTTPException(
                status_code=502,
                detail=f"Failed to create Realtime session: {response.text}"
            )

        session_data = response.json()
        logger.info(f"Realtime token response keys: {list(session_data.keys())}")

        # Return the full responses
        return session_data

    except httpx.TimeoutException:
        logger.error("Timeout connecting to OpenAI Realtime API")
        raise HTTPException(status_code=504, detail="Timeout connecting to OpenAI Realtime API")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Realtime token error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Endpoint 2, RAG search,called by function calling during live session

@router.post("/rag-search", response_model=RAGSearchResponse)
async def rag_search(request: RAGSearchRequest):
    logger.info(f"RAG search called: query='{request.query}' agent_id='{request.agent_id}'")

    if not request.query or not request.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    try:
        agent_id = request.agent_id if request.agent_id != "supervisor" else "hr"

       
        # automatically uses KB_REMOTE_URL (VM) if configured, otherwise
        # falls back to local Qdrant.( imported from domain.tools.rag_tools)
        context = await _search_qdrant_knowledge_base(
            query=request.query.strip(),
            agent_id=agent_id,
            k=request.top_k,
        )

        unavailable_markers = (
            "No relevant documents found.",
            "No relevant vector documents found.",
        )
        if (
            context in unavailable_markers
            or context.startswith("[KB_UNAVAILABLE]")
            or context.startswith("[QDRANT_UNAVAILABLE]")
        ):
            logger.info(f"RAG search: '{request.query[:60]}' → no results for {agent_id}")
            return RAGSearchResponse(results=[])

        logger.info(f"RAG search: '{request.query[:60]}' → got context from {agent_id}")
        return RAGSearchResponse(results=[{"content": context, "source": ""}])

    except Exception as e:
        logger.error(f"RAG search error: {e}")
        return RAGSearchResponse(results=[])