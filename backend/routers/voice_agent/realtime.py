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
import re

import httpx
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/realtime", tags=["realtime"])

# Full agent pipeline — voice agent calls this for every user question
CHAT_API_URL = "http://localhost:8000/api/v1/chat"

# Model identifiers
OPENAI_REALTIME_MODEL = "gpt-realtime"
GEMINI_LIVE_MODEL     = "gemini-live-2.5-flash-native-audio"

# Browser audio format; used to keep the stream close to real-time speed.
AUDIO_SAMPLE_RATE_HZ = 16000
AUDIO_BYTES_PER_SAMPLE = 2
MAX_AUDIO_LEAD_SECONDS = 0.5

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
"{USER_FIRST_NAME}", use the name in your next response. Do not restart spoken audio.

TOOL USE: You may briefly acknowledge a question once before calling ask_workmate_ai.
Then wait for the tool result. Do not repeat the call while it is pending.
Speak the returned answer once, then wait for the user to speak again.
Never call the tool with an empty question."""


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

def _active_provider() -> str:
    provider_override = (
        getattr(settings, "VOICE_PROVIDER", "")
        or os.getenv("VOICE_PROVIDER", "")
    ).strip().lower()

    # Keep an explicit OpenAI override for operational rollback. Otherwise,
    # prefer Gemini and verify that its credentials can actually mint a token.
    if provider_override == "openai":
        return "openai" if settings.OPENAI_API_KEY else "none"

    if settings.GOOGLE_APPLICATION_CREDENTIALS:
        try:
            _get_vertex_access_token()
            return "gemini"
        except Exception as e:
            logger.warning(f"Gemini credentials failed ({e}) — falling back to OpenAI")

    if settings.OPENAI_API_KEY:
        return "openai"

    return "none"


# which provider is active
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


#  OpenAI ephemeral token for WebRTC
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


#  Vertex AI access token from service account
def _get_vertex_access_token() -> str:
    import google.auth
    import google.auth.transport.requests
    import google.oauth2.service_account

    cred_path = settings.GOOGLE_APPLICATION_CREDENTIALS
    if not cred_path:
        raise RuntimeError("GOOGLE_APPLICATION_CREDENTIALS not set in .env")

    if not os.path.isabs(cred_path):
        backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cred_path = os.path.join(backend_dir, cred_path)

    if not os.path.exists(cred_path):
        raise RuntimeError(f"service-account.json not found at: {cred_path}")

    # load credentials directly from the file every time — bypasses any cache
    credentials = google.oauth2.service_account.Credentials.from_service_account_file(
        cred_path,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    auth_req = google.auth.transport.requests.Request()
    credentials.refresh(auth_req)
    logger.info(f"Vertex AI token generated for project: {credentials.project_id or 'unknown'} / service account: {credentials.service_account_email}")
    return credentials.token


# call the full agent pipeline and return a complete answer
async def _ask_agent(
    question: str,
    user_id: str,
    user_name: str,
    thread_id: str,
    auth_token: str,
) -> str:
    """
    Sends the user's question to /api/v1/chat with stream=False.
    Returns the complete answer text.
    All routing, RAG search, leave balance, and guardrails happen inside the pipeline.
    """
    try:
        async with httpx.AsyncClient(timeout=settings.VOICE_CHAT_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                CHAT_API_URL,
                headers={"Authorization": f"Bearer {auth_token}"} if auth_token else {},
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
    except httpx.TimeoutException:
        logger.error("Chat API timed out")
        return "That took too long. Please try asking again."
    except Exception as e:
        logger.error(f"Chat API call failed: {e}")
        return "I had trouble connecting. Please try again."


def _estimate_chunk_seconds(b64_data: str) -> float:
    """Estimate the duration of a base64 PCM16 mono audio chunk."""
    approx_bytes = (len(b64_data) * 3) // 4
    samples = approx_bytes / AUDIO_BYTES_PER_SAMPLE
    return samples / AUDIO_SAMPLE_RATE_HZ


def _normalize_tool_question(question: str) -> str:
    """Build a stable key for coalescing repeated in-flight voice lookups."""
    normalized = re.sub(r"\s+", " ", question.strip().casefold())
    return normalized.rstrip(".?!")


# Gemini Live WebSocket proxy
@router.websocket("/ws/voice")
async def gemini_voice_proxy(websocket: WebSocket):
    await websocket.accept()
    logger.info("Browser WebSocket connected — starting Gemini Live proxy")

    try:
        import websockets as ws_lib

        try:
            access_token = _get_vertex_access_token()
        except Exception as e:
            logger.warning(f"Vertex AI auth failed at session start: {e}")
            if settings.OPENAI_API_KEY:
                logger.info("Falling back to OpenAI — notifying frontend")
                await websocket.send_text(json.dumps({
                    "type": "fallback_to_openai"
                }))
            else:
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "message": f"Vertex AI auth failed and no OpenAI key configured: {str(e)}"
                }))
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
            session_auth_token: str = ""
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
                msg = json.loads(raw)
                if msg.get("type") == "user_identity":
                    session_email = msg.get("user_id", "")
                    session_name  = msg.get("user_name", "")
                    session_auth_token = msg.get("auth_token", "")
                    logger.info(f"Voice user identity received: {session_email[:6]}...")
            except asyncio.TimeoutError:
                logger.warning("No identity message within 10s — continuing as anonymous")
            except Exception as e:
                logger.warning(f"Identity message error: {e}")

            # build personalised system prompt with the user's first name
            first_name = session_name.split()[0] if session_name else "there"
            personalized_prompt = VOICE_SYSTEM_PROMPT.replace("{USER_FIRST_NAME}", first_name)

            # send setup to Gemini ONCE , after we have the name
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

            agent_tasks = {}
            inflight_questions = {}
            seen_call_ids = set()
            agent_lock = asyncio.Lock()
            setup_complete = asyncio.Event()

            async def run_agent_and_respond(call, question_key, request_group):
                call_id = call["id"]
                question = (call.get("args") or {}).get("question", "")
                try:
                    if not isinstance(question, str) or not question.strip():
                        result = {"error": "The question is empty. Ask the user to repeat their question."}
                    else:
                        # Serialize requests that share chat history. A new tool
                        # call does not mean the user interrupted an older one.
                        async with agent_lock:
                            answer = await _ask_agent(
                                question=question.strip(),
                                user_id=session_email,
                                user_name=session_name,
                                thread_id=voice_thread,
                                auth_token=session_auth_token,
                            )
                        logger.info("Voice tool result: id=%s, %d chars", call_id, len(answer))
                        result = {"output": answer}

                    # Gemini can emit the same question again under a new call
                    # ID while the first lookup is pending. Complete every call
                    # together so the provider sees one resolved tool batch,
                    # while Workmate itself executes only once.
                    function_responses = [{
                        "id": grouped_call["id"],
                        "name": grouped_call["name"],
                        "response": result,
                    } for grouped_call in request_group["calls"]]
                    await gemini_ws.send(json.dumps({
                        "tool_response": {"function_responses": function_responses}
                    }))
                except asyncio.CancelledError:
                    # Cancelled calls must not receive empty or stale results.
                    logger.info("Voice tool cancelled: id=%s", call_id)
                    raise
                except Exception:
                    logger.exception("Voice tool response failed: id=%s", call_id)
                finally:
                    for grouped_call in request_group["calls"]:
                        agent_tasks.pop(grouped_call["id"], None)
                    if inflight_questions.get(question_key) is request_group:
                        inflight_questions.pop(question_key, None)

            async def browser_to_gemini():
                loop = asyncio.get_running_loop()
                stream_start_time = None
                audio_seconds_sent = 0.0

                try:
                    while True:
                        raw = await websocket.receive_text()
                        msg = json.loads(raw)
                        if msg.get("type") == "audio":
                            await setup_complete.wait()
                            if stream_start_time is None:
                                stream_start_time = loop.time()
                                audio_seconds_sent = 0.0

                            audio_seconds_sent += _estimate_chunk_seconds(msg["data"])
                            elapsed_real_time = loop.time() - stream_start_time
                            lead = audio_seconds_sent - elapsed_real_time
                            if lead > MAX_AUDIO_LEAD_SECONDS:
                                await asyncio.sleep(lead - MAX_AUDIO_LEAD_SECONDS)

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
                        if "setupComplete" in data:
                            setup_complete.set()
                        server_content = data.get("serverContent", {})

                        if server_content.get("interrupted"):
                            await websocket.send_text(json.dumps({"type": "stop_audio"}))

                        for call_id in data.get("toolCallCancellation", {}).get("ids", []):
                            task = agent_tasks.get(call_id)
                            if task is not None:
                                task.cancel()

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

                        # Only provider cancellation events cancel pending calls.
                        # Injecting client_content with turn_complete while waiting
                        # starts another generation and can repeat the tool call.
                        for call in data.get("toolCall", {}).get("functionCalls", []):
                            if call.get("name") != "ask_workmate_ai":
                                continue
                            call_id = call.get("id")
                            if not call_id:
                                logger.warning("Ignoring voice tool call without an id")
                                continue
                            if call_id in seen_call_ids:
                                logger.info("Ignoring duplicate voice tool call: id=%s", call_id)
                                continue
                            seen_call_ids.add(call_id)
                            question = (call.get("args") or {}).get("question", "")
                            question_key = (
                                _normalize_tool_question(question)
                                if isinstance(question, str) and question.strip()
                                else f"empty:{call_id}"
                            )
                            existing_group = inflight_questions.get(question_key)
                            if existing_group is not None:
                                existing_group["calls"].append(call)
                                agent_tasks[call_id] = existing_group["task"]
                                logger.info(
                                    "Coalescing duplicate voice tool call: id=%s primary=%s",
                                    call_id,
                                    existing_group["calls"][0]["id"],
                                )
                                continue
                            logger.info("Voice tool call: id=%s", call_id)
                            request_group = {"calls": [call], "task": None}
                            task = asyncio.create_task(
                                run_agent_and_respond(call, question_key, request_group)
                            )
                            request_group["task"] = task
                            inflight_questions[question_key] = request_group
                            agent_tasks[call_id] = task

                except Exception as e:
                    logger.error(f"gemini_to_browser error: {e}")
                    try:
                        await websocket.send_text(json.dumps({"type": "error", "message": str(e)}))
                    except Exception:
                        pass

            relay_tasks = [
                asyncio.create_task(browser_to_gemini()),
                asyncio.create_task(gemini_to_browser()),
            ]
            try:
                # Either peer ending the call ends the whole session.
                await asyncio.wait(relay_tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in relay_tasks:
                    task.cancel()
                await asyncio.gather(*relay_tasks, return_exceptions=True)
                pending = list(agent_tasks.values())
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)

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
