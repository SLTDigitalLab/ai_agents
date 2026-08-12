"""
realtime.py — Live Voice Agent router for Workmate AI

Provider switching — edit .env only, no code changes needed:

  USE GEMINI (Vertex AI):
    GOOGLE_APPLICATION_CREDENTIALS=path_to_your_google_credentials.json
    PROJECT_ID=your_gcp_project_id
    LOCATION=your_gcp_project_location

  USE OPENAI:
    OPENAI_API_KEY=your_key
    # comment out GOOGLE_APPLICATION_CREDENTIALS

Endpoints:
  GET  /api/v1/realtime/provider    — which provider is active
  GET  /api/v1/realtime/token       — OpenAI ephemeral token (WebRTC)
  WS   /api/v1/realtime/ws/voice    — Gemini Live proxy (WebSocket)
"""

import asyncio
import json
import logging
import os
import random

import httpx
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/realtime", tags=["realtime"])

# Full agent pipeline — voice agent calls this for every user question
CHAT_API_URL = "http://localhost:8000/api/v1/chat"

# Model identifiers
OPENAI_REALTIME_MODEL = "gpt-realtime-2"
GEMINI_LIVE_MODEL     = "gemini-live-2.5-flash-preview-native-audio-09-2025"

# System prompt
VOICE_SYSTEM_PROMPT = """You are Workmate AI, the intelligent voice assistant for SLTMobitel employees.
You help employees with questions about HR policies, Finance, IT support, Admin procedures,
internal audit (CIA), and business processes.

You are having a live voice conversation. Keep your responses:
- Concise and clear — this is a spoken conversation, not a chat interface
- Natural sounding — avoid bullet points or markdown formatting
- Accurate — always use the ask_workmate_ai function when answering any company-specific question

When you don't have enough information, use ask_workmate_ai before answering.
If a question is completely outside SLTMobitel workplace topics, politely say you
can only help with work-related questions.

CRITICAL RULES — follow these in every single response without exception:

RULE 1 — GREETING: At the very start of this conversation, you MUST say exactly:
"Hello {USER_FIRST_NAME}! I am Workmate AI, your SLTMobitel workplace assistant. 
I can help you with HR policies, leave balances, finance, IT support, and more. 
What would you like to know today?"
Do NOT paraphrase this. Do NOT skip the name. Say it exactly.

RULE 2 — EVERY RESPONSE: Every single answer you give MUST begin with "{USER_FIRST_NAME}, " 
followed by your answer. No exceptions. Even short answers must start with the name.
For example: "{USER_FIRST_NAME}, your annual leave balance is 14 days."
Or: "{USER_FIRST_NAME}, to apply for leave you need to..."

RULE 3 — NEVER skip the name. If you are about to respond without starting with 
"{USER_FIRST_NAME}", stop and restart your response with the name first."""


# Single tool — routes all questions through the full agent pipeline
WORKMATE_TOOL = {
    "name": "ask_workmate_ai",
    "description": (
        "Send the user's question to the Workmate AI agent pipeline. "
        "Use this for ALL questions — HR policies, leave balance, finance, "
        "IT support, admin, or any SLTMobitel workplace topic. "
        "The agent automatically searches the knowledge base or calls "
        "external systems as needed."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The user's question exactly as spoken",
            }
        },
        "required": ["question"],
    },
}

# Filler phrases per language — spoken while the agent pipeline runs
# so the user never hears silence during the search
FILLERS = {
    "en": [
        "Sure, let me check that for you.",
        "Got it, one moment while I look that up.",
        "Okay, checking that now.",
        "Let me find that information for you.",
        "Sure, just a moment.",
        "Right, let me look into that.",
    ],
    "si": [
        "හරි, මමඒක බලන්නම්.",
        "හොඳයි, එක මොහොතක් රැඳෙන්න.",
        "ඒක දැන් සොයා බලනවා.",
        "ඒ තොරතුරු ලබා ගන්නම්.",
    ],
    "ta": [
        "சரி, நான் அதை சரிபார்க்கிறேன்.",
        "ஒரு நிமிடம், நான் தேடுகிறேன்.",
        "சரி, இப்போது தேடுகிறேன்.",
        "அந்த தகவலை தருகிறேன்.",
    ],
}


def _detect_language(text: str) -> str:
    """
    Lightweight script detection — checks Unicode ranges.
    Returns 'si' for Sinhala, 'ta' for Tamil, 'en' for everything else.
    No external library needed.
    """
    for ch in text:
        cp = ord(ch)
        if 0x0D80 <= cp <= 0x0DFF:   # Sinhala Unicode block
            return "si"
        if 0x0B80 <= cp <= 0x0BFF:   # Tamil Unicode block
            return "ta"
    return "en"


def _pick_filler(question: str) -> str:
    """Pick a random filler phrase that matches the language of the question."""
    lang = _detect_language(question)
    return random.choice(FILLERS.get(lang, FILLERS["en"]))


def _active_provider() -> str:
    if settings.GOOGLE_APPLICATION_CREDENTIALS:
        return "gemini"
    if settings.OPENAI_API_KEY:
        return "openai"
    return "none"


# Endpoint: which provider is active
@router.get("/provider")
async def get_voice_provider():
    provider = _active_provider()
    if provider == "none":
        raise HTTPException(
            status_code=500,
            detail="No voice provider configured. Set GOOGLE_APPLICATION_CREDENTIALS or OPENAI_API_KEY in .env.",
        )
    model = GEMINI_LIVE_MODEL if provider == "gemini" else OPENAI_REALTIME_MODEL
    logger.info(f"Voice provider: {provider} / {model}")
    return {"provider": provider, "model": model}


# Endpoint: OpenAI ephemeral token for WebRTC
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
                        "audio": {"output": {"voice": "ash"}},
                    }
                },
            )
        if response.status_code != 200:
            logger.error(f"OpenAI token error: {response.status_code} — {response.text}")
            raise HTTPException(status_code=502, detail=f"Failed to create OpenAI session: {response.text}")

        data = response.json()
        logger.info(f"OpenAI ephemeral token generated — voice: ash")
        return data

    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Timeout connecting to OpenAI")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"OpenAI token error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Helper: Vertex AI access token from service account
def _get_vertex_access_token() -> str:
    import google.auth
    import google.auth.transport.requests

    cred_path = settings.GOOGLE_APPLICATION_CREDENTIALS
    if not cred_path:
        raise RuntimeError("GOOGLE_APPLICATION_CREDENTIALS not set in .env")

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


# Helper: keep Gemini WebSocket alive while waiting for a slow operation.
# Sends a lightweight ping-style message every interval_sec seconds.
# The done_event is set by the caller when the wait is over.
async def _gemini_keepalive(gemini_ws, done_event: asyncio.Event, interval_sec: float = 5.0):
    """
    Gemini Live closes with 1011 if nothing is sent for ~10 seconds.
    This coroutine sends an empty realtime_input heartbeat to keep
    the connection open while the agent pipeline is running.
    """
    try:
        while not done_event.is_set():
            await asyncio.sleep(interval_sec)
            if done_event.is_set():
                break
            try:
                # empty media chunk — valid no-op that resets the keepalive timer
                await gemini_ws.send(json.dumps({
                    "realtime_input": {"media_chunks": []}
                }))
                logger.debug("Gemini keepalive ping sent")
            except Exception:
                break
    except asyncio.CancelledError:
        pass


# Helper: call the full agent pipeline and return a complete answer
async def _ask_agent(question: str, user_id: str, user_name: str, thread_id: str) -> str:
    """
    Sends the user's question to /api/v1/chat with stream=False.
    Returns the complete answer text.
    All routing, RAG search, leave balance, and guardrails happen inside the pipeline.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                CHAT_API_URL,
                json={
                    "message":   question,
                    "agent_id":  "supervisor",
                    "user_id":   user_id or "anonymous",
                    "user_name": user_name or "",
                    "thread_id": thread_id,
                    "stream":    False,
                },
            )
        if resp.status_code == 200:
            answer = resp.json().get("response", "").strip()
            if answer:
                return answer
            logger.warning("Chat API returned empty response")
            return "I couldn't find the information right now. Please try again."
        else:
            logger.error(f"Chat API returned {resp.status_code}: {resp.text[:200]}")
            return "I had trouble finding that information. Please try again."
    except httpx.TimeoutError:
        logger.error("Chat API timed out")
        return "That took too long. Please try asking again."
    except Exception as e:
        logger.error(f"Chat API call failed: {e}")
        return "I had trouble connecting. Please try again."


# Endpoint: Gemini Live WebSocket proxy
@router.websocket("/ws/voice")
async def gemini_voice_proxy(websocket: WebSocket):
    await websocket.accept()
    logger.info("Browser WebSocket connected — starting Gemini Live proxy")

    try:
        import websockets as ws_lib

        try:
            access_token = _get_vertex_access_token()
        except Exception as e:
            await websocket.send_text(json.dumps({"type": "error", "message": f"Vertex AI auth failed: {str(e)}"}))
            return

        project_id = settings.PROJECT_ID
        if not project_id:
            await websocket.send_text(json.dumps({"type": "error", "message": "PROJECT_ID not set in .env"}))
            return

        region = "us-central1"
        gemini_url = (
            f"wss://{region}-aiplatform.googleapis.com"
            f"/ws/google.cloud.aiplatform.v1.LlmBidiService/BidiGenerateContent"
            f"?access_token={access_token}"
        )
        model_resource = (
            f"projects/{project_id}/locations/{region}"
            f"/publishers/google/models/{GEMINI_LIVE_MODEL}"
        )

        logger.info(f"Connecting to Gemini Live: {region}-aiplatform.googleapis.com model={GEMINI_LIVE_MODEL}")

        async with ws_lib.connect(
            gemini_url,
            additional_headers={"Content-Type": "application/json"},
            max_size=10 * 1024 * 1024,
        ) as gemini_ws:

            
            logger.info("Gemini Live WebSocket connected")

            # send ready to browser so it sends user identity
            await websocket.send_text(json.dumps({"type": "ready"}))

            # receive user identity from browser
            session_email: str = ""
            session_name: str = ""
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
                msg = json.loads(raw)
                if msg.get("type") == "user_identity":
                    session_email = msg.get("user_id", "")
                    session_name  = msg.get("user_name", "")
                    logger.info(f"Voice user identity received: {session_email[:6]}...")
            except asyncio.TimeoutError:
                logger.warning("No identity message within 10s — continuing as anonymous")
            except Exception as e:
                logger.warning(f"Identity message error: {e}")

            # build personalised system prompt with the user's first name
            first_name = session_name.split()[0] if session_name else "there"
            personalized_prompt = VOICE_SYSTEM_PROMPT.replace("{USER_FIRST_NAME}", first_name)

            # send setup to Gemini ONCE — after we have the name
            setup = {
                "setup": {
                    "model": model_resource,
                    "generation_config": {
                        "response_modalities": ["AUDIO"],
                        "speech_config": {
                            "voice_config": {
                                "prebuilt_voice_config": {"voice_name": "Alnilam"}
                            }
                        },
                    },
                    "system_instruction": {"parts": [{"text": personalized_prompt}]},
                    "tools": [{"function_declarations": [WORKMATE_TOOL]}],
                }
            }
            await gemini_ws.send(json.dumps(setup))
            logger.info(f"Gemini Live setup sent — voice: Alnilam — user: {first_name}")

            voice_thread = f"voice_{id(websocket)}"

            # tracks the currently running agent task so it can be cancelled on interruption
            current_agent_task: asyncio.Task | None = None
            current_agent_lock = asyncio.Lock()

            async def browser_to_gemini():
                try:
                    while True:
                        raw = await websocket.receive_text()
                        msg = json.loads(raw)
                        if msg.get("type") == "audio":
                            await gemini_ws.send(json.dumps({
                                "realtime_input": {
                                    "media_chunks": [{
                                        "mime_type": "audio/pcm;rate=16000",
                                        "data": msg["data"],
                                    }]
                                }
                            }))
                        elif msg.get("type") == "end":
                            logger.info("Browser sent end signal")
                            break
                except WebSocketDisconnect:
                    logger.info("Browser WebSocket disconnected")
                except Exception as e:
                    logger.error(f"browser_to_gemini error: {e}")

            async def gemini_to_browser():
                try:
                    async for raw_msg in gemini_ws:
                        data = json.loads(raw_msg)
                        server_content = data.get("serverContent", {})

                        # forward audio and transcript to browser
                        for part in server_content.get("modelTurn", {}).get("parts", []):
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

                        # handle tool calls
                        for call in data.get("toolCall", {}).get("functionCalls", []):
                                    if call.get("name") == "ask_workmate_ai":
                                        question = call.get("args", {}).get("question", "")
                                        logger.info(f"Voice tool call: '{question[:80]}'")

                                        # tell browser to pause mic so filler is not interrupted
                                        await websocket.send_text(json.dumps({"type": "mic_pause"}))

                                        # send filler to Gemini
                                        filler = _pick_filler(question)
                                        await gemini_ws.send(json.dumps({
                                            "client_content": {
                                                "turns": [{"role": "model", "parts": [{"text": filler}]}],
                                                "turn_complete": True,   # ← change False to True
                                            }
                                        }))
                                        logger.info(f"Filler sent ({_detect_language(question)}): '{filler}'")

                                        # wait a moment for filler to start playing before pipeline starts
                                        await asyncio.sleep(1.5)

                                        # call agent pipeline with keepalive
                                        done_event = asyncio.Event()
                                        keepalive_task = asyncio.create_task(
                                            _gemini_keepalive(gemini_ws, done_event)
                                        )
                                        try:
                                            answer = await _ask_agent(
                                                question=question,
                                                user_id=session_email,
                                                user_name=session_name,
                                                thread_id=voice_thread,
                                            )
                                        finally:
                                            done_event.set()
                                            keepalive_task.cancel()
                                            try:
                                                await keepalive_task
                                            except asyncio.CancelledError:
                                                pass

                                        logger.info(f"Agent answer: {len(answer)} chars")

                                        # resume mic after answer is ready
                                        await websocket.send_text(json.dumps({"type": "mic_resume"}))

                                        await gemini_ws.send(json.dumps({
                                            "tool_response": {
                                                "function_responses": [{
                                                    "name": "ask_workmate_ai",
                                                    "response": {"output": answer},
                                                }]
                                            }
                                        }))
                                        logger.info("Tool response sent")

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
            await websocket.send_text(json.dumps({"type": "error", "message": f"Gemini Live connection failed: {str(e)}"}))
        except Exception:
            pass
    finally:
        logger.info("Gemini Live voice session ended")
        try:
            await websocket.send_text(json.dumps({"type": "session_end"}))
        except Exception:
            pass