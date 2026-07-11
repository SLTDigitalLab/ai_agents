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
  GET  /api/v1/realtime/provider      — which provider is active
  GET  /api/v1/realtime/token         — OpenAI ephemeral token (WebRTC)
  WS   /api/v1/realtime/ws/voice      — Gemini Live proxy (WebSocket)
  POST /api/v1/realtime/rag-search    — RAG search (both providers)
"""

import asyncio
import base64
import json
import logging
import os
import httpx
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from core.config import settings
from domain.tools.rag_tools import _search_qdrant_knowledge_base

#for leave balance
import asyncio
import secrets
import time
from domain.tools.api_tools import fetch_leave_balance_for_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/realtime", tags=["realtime"])

#  Model identifiers 
OPENAI_REALTIME_MODEL  = "gpt-realtime-2"
GEMINI_LIVE_MODEL      = "gemini-live-2.5-flash-preview-native-audio-09-2025"

# Shared system prompt 
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

#  Shared tool definition 
KB_TOOL_NAME        = "search_knowledge_base"
KB_TOOL_DESCRIPTION = (
    "Search the SLTMobitel internal knowledge base for HR policies, leave, "
    "benefits, finance, IT support, admin procedures, or CIA compliance. "
    "Always call this before answering any company-specific question."
)
KB_TOOL_PARAMETERS  = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "The search query",
        },
        "agent_id": {
            "type": "string",
            "description": "Which knowledge base: hr, finance, admin, it, cia, process",
            "enum": ["supervisor", "hr", "finance", "admin", "it", "cia", "process"],
        },
    },
    "required": ["query"],
}

#  Voice session store (email lookup by short-lived token) 
# { session_token: { "email": "...", "expires_at": float } }
_voice_sessions: dict[str, dict] = {}
_SESSION_TTL = 300  # 5 minutes

def _store_session(email: str) -> str:
    """Create a short-lived session token mapped to a verified email."""
    token = secrets.token_urlsafe(32)
    _voice_sessions[token] = {"email": email, "expires_at": time.time() + _SESSION_TTL}
    # Clean up expired entries
    expired = [k for k, v in _voice_sessions.items() if v["expires_at"] < time.time()]
    for k in expired:
        del _voice_sessions[k]
    return token

def _resolve_session(token: str) -> str | None:
    """Resolve a session token to an email. Returns None if expired or not found."""
    entry = _voice_sessions.get(token)
    if not entry:
        return None
    if entry["expires_at"] < time.time():
        del _voice_sessions[token]
        return None
    return entry["email"]

#  Leave balance tool definition (added to both Gemini and OpenAI tool lists) ─
LEAVE_TOOL_NAME        = "get_leave_balance"
LEAVE_TOOL_DESCRIPTION = (
    "Look up the authenticated employee's personal leave balance from the HR system. "
    "Call this when the user asks about their leave balance, remaining leave days, "
    "annual leave, casual leave, or sick leave."
)
LEAVE_TOOL_PARAMETERS  = {
    "type": "object",
    "properties": {},
    "required": [],
}

#  Schemas
class RAGSearchRequest(BaseModel):
    query:    str
    agent_id: str = "supervisor"
    top_k:    int = 5

class RAGSearchResponse(BaseModel):
    results: list[dict]


#  Helper: detect active provider 
def _active_provider() -> str:
    """
    Returns 'gemini' if GOOGLE_APPLICATION_CREDENTIALS is set, else 'openai'.
    To switch providers just comment / uncomment the relevant .env lines.
    """
    if settings.GOOGLE_APPLICATION_CREDENTIALS:
        return "gemini"
    if settings.OPENAI_API_KEY:
        return "openai"
    return "none"


#  Endpoint: provider detection 
@router.get("/provider")
async def get_voice_provider():
    """
    Returns the active voice provider based on which credentials are set in .env.
    Frontend calls this on page load to decide which connection path to use.
    """
    provider = _active_provider()
    if provider == "none":
        raise HTTPException(
            status_code=500,
            detail="No voice provider configured. Set GOOGLE_APPLICATION_CREDENTIALS or OPENAI_API_KEY in .env."
        )
    model = GEMINI_LIVE_MODEL if provider == "gemini" else OPENAI_REALTIME_MODEL
    logger.info(f"Voice provider: {provider} / {model}")
    return {"provider": provider, "model": model}


#  Endpoint: OpenAI ephemeral token (unchanged) 
@router.get("/token")
async def get_realtime_token():
    """
    Generates an OpenAI ephemeral token for WebRTC.
    Only used when provider == 'openai'.
    """
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
    

#  Endpoint: exchange MSAL token for voice session token 
class SessionTokenRequest(BaseModel):
    msal_token: str

@router.post("/session-token")
async def create_voice_session(request: SessionTokenRequest):
    """
    Accepts a Microsoft MSAL access token from the frontend.
    Verifies it by calling Microsoft Graph /me endpoint.
    Returns a short-lived session token the WebSocket will use to identify the user.
    The user's email never travels over the WebSocket connection.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            graph_res = await client.get(
                "https://graph.microsoft.com/v1.0/me",
                headers={"Authorization": f"Bearer {request.msal_token}"},
            )

        if graph_res.status_code != 200:
            raise HTTPException(status_code=401, detail="Invalid or expired MSAL token")

        profile = graph_res.json()
        # Use userPrincipalName (the intranet email) — same field the chat agent uses
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


#  Helper: get Vertex AI access token from service account 
def _get_vertex_access_token() -> str:
    """
    Uses the service-account.json to get a short-lived Google access token.
    Resolves the credential path relative to the backend directory.
    """
    import google.auth
    import google.auth.transport.requests

    cred_path = settings.GOOGLE_APPLICATION_CREDENTIALS
    if not cred_path:
        raise RuntimeError("GOOGLE_APPLICATION_CREDENTIALS not set in .env")

    # Resolve relative path from backend root
    if not os.path.isabs(cred_path):
        backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cred_path = os.path.join(backend_dir, cred_path)

    if not os.path.exists(cred_path):
        raise RuntimeError(f"service-account.json not found at: {cred_path}")

    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = cred_path

    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    auth_req = google.auth.transport.requests.Request()
    credentials.refresh(auth_req)
    return credentials.token


#  Endpoint: Gemini Live WebSocket proxy 
@router.websocket("/ws/voice")
async def gemini_voice_proxy(websocket: WebSocket):
    await websocket.accept()
    logger.info("Browser WebSocket connected — starting Gemini Live proxy")

    #  Step 0: wait for auth message (first message from browser) 
    session_email: str = ""
    

    try:
        import websockets as ws_lib

        try:
            access_token = _get_vertex_access_token()
        except Exception as e:
            await websocket.send_text(json.dumps({
                "type": "error",
                "message": f"Vertex AI auth failed: {str(e)}"
            }))
            return

        project_id = settings.PROJECT_ID
        location   = settings.LOCATION or "us-central1"

        if not project_id:
            await websocket.send_text(json.dumps({
                "type": "error",
                "message": "PROJECT_ID not set in .env"
            }))
            return

        region = "us-central1"
        host = f"{region}-aiplatform.googleapis.com"
        path = "google.cloud.aiplatform.v1.LlmBidiService/BidiGenerateContent"
        gemini_url = f"wss://{host}/ws/{path}?access_token={access_token}"
        model_resource = (
            f"projects/{project_id}/locations/{region}"
            f"/publishers/google/models/{GEMINI_LIVE_MODEL}"
        )

        logger.info(f"Connecting to Gemini Live: {host} model={GEMINI_LIVE_MODEL}")

        async with ws_lib.connect(
            gemini_url,
            additional_headers={"Content-Type": "application/json"},
            max_size=10 * 1024 * 1024,
        ) as gemini_ws:

            logger.info("Gemini Live WebSocket connected")

            setup = {
                "setup": {
                    "model": model_resource,
                    "generation_config": {
                        "response_modalities": ["AUDIO"],
                        "speech_config": {
                            "voice_config": {
                                "prebuilt_voice_config": {
                                    "voice_name": "Aoede"
                                }
                            }
                        },
                    },
                    "system_instruction": {
                        "parts": [{"text": VOICE_SYSTEM_PROMPT}]
                    },
                    "tools": [
                        {
                            "function_declarations": [
                                {
                                    "name": KB_TOOL_NAME,
                                    "description": KB_TOOL_DESCRIPTION,
                                    "parameters": KB_TOOL_PARAMETERS,
                                },
                                {
                                    "name": LEAVE_TOOL_NAME,
                                    "description": LEAVE_TOOL_DESCRIPTION,
                                    "parameters": LEAVE_TOOL_PARAMETERS,
                                },
                            ]
                        }
                    ],
                }
            }
            await gemini_ws.send(json.dumps(setup))
            logger.info("Gemini Live setup sent")

            await websocket.send_text(json.dumps({"type": "ready"}))
            #  Wait for auth message from browser (sent right after ready) 
            try:
                raw_auth = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
                auth_msg = json.loads(raw_auth)
                if auth_msg.get("type") == "auth":
                    session_email = _resolve_session(auth_msg.get("session_token", "")) or ""
                    if session_email:
                        logger.info(f"Voice session authenticated: {session_email[:4]}...@...")
                    else:
                        logger.warning("Voice session: invalid or expired session token")
            except asyncio.TimeoutError:
                logger.warning("No auth message within 10s — continuing unauthenticated")
            except Exception as e:
                logger.warning(f"Auth message error: {e}")

            #  browser → Gemini 
            async def browser_to_gemini():
                try:
                    while True:
                        raw = await websocket.receive_text()
                        msg = json.loads(raw)
                        if msg.get("type") == "audio":
                            await gemini_ws.send(json.dumps({
                                "realtime_input": {
                                    "media_chunks": [
                                        {
                                            "mime_type": "audio/pcm;rate=16000",
                                            "data": msg["data"],
                                        }
                                    ]
                                }
                            }))
                        elif msg.get("type") == "end":
                            logger.info("Browser sent end signal")
                            break
                except WebSocketDisconnect:
                    logger.info("Browser WebSocket disconnected")
                except Exception as e:
                    logger.error(f"browser_to_gemini error: {e}")

            #  Gemini → browser 
            async def gemini_to_browser():
                try:
                    async for raw_msg in gemini_ws:
                        data = json.loads(raw_msg)
                        server_content = data.get("serverContent", {})

                        parts = server_content.get("modelTurn", {}).get("parts", [])
                        for part in parts:
                            if "inlineData" in part:
                                inline = part["inlineData"]
                                await websocket.send_text(json.dumps({
                                    "type":      "audio",
                                    "data":      inline.get("data", ""),
                                    "mime_type": inline.get("mimeType", "audio/pcm;rate=24000"),
                                }))
                            elif "text" in part:
                                await websocket.send_text(json.dumps({
                                    "type": "transcript",
                                    "role": "model",
                                    "text": part["text"],
                                }))

                        input_text = server_content.get("inputTranscription", {}).get("text", "")
                        if input_text:
                            await websocket.send_text(json.dumps({
                                "type": "transcript",
                                "role": "user",
                                "text": input_text,
                            }))

                        if server_content.get("turnComplete"):
                            await websocket.send_text(json.dumps({"type": "turn_complete"}))

                        tool_calls = data.get("toolCall", {}).get("functionCalls", [])
                        for call in tool_calls:
                            name = call.get("name", "")
                            args = call.get("args", {})

                            if name == KB_TOOL_NAME:
                                query    = args.get("query", "")
                                agent_id = args.get("agent_id", "hr")
                                logger.info(f"Gemini tool call: {KB_TOOL_NAME} query='{query}' agent='{agent_id}'")
                                context = await _search_qdrant_knowledge_base(
                                    query=query,
                                    agent_id=agent_id if agent_id != "supervisor" else "hr",
                                    k=5,
                                )
                                await gemini_ws.send(json.dumps({
                                    "tool_response": {
                                        "function_responses": [
                                            {
                                                "name": KB_TOOL_NAME,
                                                "response": {
                                                    "output": context or "No relevant information found."
                                                },
                                            }
                                        ]
                                    }
                                }))

                            elif name == LEAVE_TOOL_NAME:
                                logger.info(f"Gemini tool call: {LEAVE_TOOL_NAME} user={session_email[:4] if session_email else 'unknown'}...")
                                if not session_email:
                                    result = "I cannot retrieve your leave balance because your identity could not be verified. Please ensure you are logged in."
                                else:
                                    result = await asyncio.to_thread(
                                        fetch_leave_balance_for_user, session_email
                                    )
                                await gemini_ws.send(json.dumps({
                                    "tool_response": {
                                        "function_responses": [
                                            {
                                                "name": LEAVE_TOOL_NAME,
                                                "response": {"output": result},
                                            }
                                        ]
                                    }
                                }))

                except Exception as e:
                    logger.error(f"gemini_to_browser error: {e}")
                    try:
                        await websocket.send_text(json.dumps({"type": "error", "message": str(e)}))
                    except Exception:
                        pass

            await asyncio.gather(browser_to_gemini(), gemini_to_browser())

    except Exception as e:
        logger.error(f"Gemini voice proxy error: {e}")
        try:
            await websocket.send_text(json.dumps({
                "type": "error",
                "message": f"Gemini Live connection failed: {str(e)}",
            }))
        except Exception:
            pass
    finally:
        logger.info("Gemini Live voice session ended")
        try:
            await websocket.send_text(json.dumps({"type": "session_end"}))
        except Exception:
            pass

#  Endpoint: leave balance lookup for OpenAI path 
class LeaveBalanceRequest(BaseModel):
    session_token: str

@router.post("/leave-balance")
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
    

#  Endpoint: RAG search (unchanged — used by both providers) 
@router.post("/rag-search", response_model=RAGSearchResponse)
async def rag_search(request: RAGSearchRequest):
    """
    Searches the existing Qdrant knowledge base.
    Called by function calling from OpenAI (via fetch) or Gemini (via proxy above).
    Reuses _search_qdrant_knowledge_base — same path as working chat agents.
    """
    logger.info(f"RAG search: query='{request.query}' agent_id='{request.agent_id}'")

    if not request.query or not request.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    try:
        agent_id = request.agent_id if request.agent_id != "supervisor" else "hr"

        context = await _search_qdrant_knowledge_base(
            query=request.query.strip(),
            agent_id=agent_id,
            k=request.top_k,
        )

        unavailable = (
            "No relevant documents found.",
            "No relevant vector documents found.",
        )
        if context in unavailable or context.startswith(("[KB_UNAVAILABLE]", "[QDRANT_UNAVAILABLE]")):
            logger.info(f"RAG search: no results for '{agent_id}'")
            return RAGSearchResponse(results=[])

        logger.info(f"RAG search: got context from '{agent_id}'")
        return RAGSearchResponse(results=[{"content": context, "source": ""}])

    except Exception as e:
        logger.error(f"RAG search error: {e}")
        return RAGSearchResponse(results=[])