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
import re
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.security import HTTPAuthorizationCredentials

from core.auth import get_verified_user
from core.chat_access import chat_identity
from core.config import settings
from services.voice_fillers import FillerScheduler
from services.voice_language_id import AudioLanguageWindow, identify_audio, warm_audio_language_model

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
GEMINI_CONNECT_ATTEMPTS = 3
GEMINI_CONNECT_BACKOFF_SECONDS = 0.5
# One authenticated employee should have one live Gemini session on this
# backend process.  This bounds accidental double-click/tab sessions, which
# otherwise consume scarce Live capacity without improving a call.
_ACTIVE_VOICE_SESSIONS: dict[str, str] = {}


def _claim_voice_session(user_id: str, session_id: str) -> bool:
    key = user_id.casefold()
    if key in _ACTIVE_VOICE_SESSIONS:
        return False
    _ACTIVE_VOICE_SESSIONS[key] = session_id
    return True


def _release_voice_session(user_id: str | None, session_id: str | None) -> None:
    if user_id and session_id and _ACTIVE_VOICE_SESSIONS.get(user_id.casefold()) == session_id:
        _ACTIVE_VOICE_SESSIONS.pop(user_id.casefold(), None)


def _is_resource_exhausted(error: Exception) -> bool:
    """Classify provider capacity errors without exposing provider details."""
    detail = str(error).casefold()
    return "resource exhausted" in detail or "resource_exhausted" in detail


async def _connect_gemini_with_backoff(ws_lib, url: str):
    """Retry only a fresh provider connection; never replay an active session."""
    last_error = None
    for attempt in range(GEMINI_CONNECT_ATTEMPTS):
        try:
            return await ws_lib.connect(
                url,
                additional_headers={"Content-Type": "application/json"},
                max_size=10 * 1024 * 1024,
            )
        except Exception as exc:
            last_error = exc
            if not _is_resource_exhausted(exc) or attempt == GEMINI_CONNECT_ATTEMPTS - 1:
                raise
            delay = GEMINI_CONNECT_BACKOFF_SECONDS * (2 ** attempt) + random.uniform(0, 0.25)
            logger.warning("Gemini Live capacity unavailable; retrying fresh connection attempt %s/%s", attempt + 2, GEMINI_CONNECT_ATTEMPTS)
            await asyncio.sleep(delay)
    raise last_error


def _voice_error_payload(error: Exception) -> dict:
    if _is_resource_exhausted(error):
        return {
            "type": "error",
            "code": "RESOURCE_EXHAUSTED",
            "message": "Voice service is busy right now. Please wait a moment and start a new call.",
        }
    return {"type": "error", "code": "VOICE_CONNECTION_FAILED", "message": "Voice connection ended unexpectedly. Please start a new call."}

# Do not set a session-wide Gemini language code: it would pin a conversation
# that needs to switch language on every completed user turn.
VOICE_LANGUAGE_EN = "en-US"
VOICE_LANGUAGE_SI = "si-LK"
VOICE_LANGUAGE_TA = "ta-LK"
VOICE_LANGUAGE_PREFERENCES = frozenset({
    VOICE_LANGUAGE_EN, VOICE_LANGUAGE_SI, VOICE_LANGUAGE_TA,
})

# Native script is authoritative. Romanized cues are deliberately conservative
# whole words so names, borrowed words, and product terms do not switch speech.
ROMANIZED_SINHALA_WORDS = frozenset({
    "hari", "kohomada", "kohomadha", "oyaata", "oyata", "mata", "karanna",
    "puluwanda", "istuti", "ayubowan", "mokakda", "mokadda",
})
ROMANIZED_TAMIL_WORDS = frozenset({
    "sari", "vanakkam", "enna", "epadi", "eppadi", "sollunga", "sollu",
    "nandri", "venum", "vendum", "seyyalam", "seiyalam", "irukku",
    "ennudaya", "ennoda", "evvalavu", "evalavu", "enakku", "solunga", "irukka",
})


def resolve_voice_language(text: str) -> str:
    """Resolve one final user turn without retaining a prior turn's locale.

    The script with most letters wins; a Sinhala/Tamil script tie favours
    Sinhala. Romanized-word matches come next, and ambiguous text is English.
    """
    if not isinstance(text, str) or not text.strip():
        return VOICE_LANGUAGE_EN
    sinhala_count = sum("\u0d80" <= char <= "\u0dff" for char in text)
    tamil_count = sum("\u0b80" <= char <= "\u0bff" for char in text)
    if sinhala_count or tamil_count:
        return VOICE_LANGUAGE_SI if sinhala_count >= tamil_count else VOICE_LANGUAGE_TA
    words = set(re.findall(r"[a-z]+", text.casefold()))
    sinhala_hits = len(words & ROMANIZED_SINHALA_WORDS)
    tamil_hits = len(words & ROMANIZED_TAMIL_WORDS)
    if sinhala_hits or tamil_hits:
        return VOICE_LANGUAGE_SI if sinhala_hits >= tamil_hits else VOICE_LANGUAGE_TA
    return VOICE_LANGUAGE_EN


def _native_script_language(text: str) -> str | None:
    """Return only an unambiguous Sinhala/Tamil script signal, if present."""
    if not isinstance(text, str):
        return None
    sinhala_count = sum("\u0d80" <= char <= "\u0dff" for char in text)
    tamil_count = sum("\u0b80" <= char <= "\u0bff" for char in text)
    if not (sinhala_count or tamil_count):
        return None
    return VOICE_LANGUAGE_SI if sinhala_count >= tamil_count else VOICE_LANGUAGE_TA


def _parse_voice_language_preference(value: object) -> str | None:
    """Accept only a user-selected response-language override or Auto."""
    return value if isinstance(value, str) and value in VOICE_LANGUAGE_PREFERENCES else None


def _language_instruction(language: str) -> str:
    # Native-audio Live chooses its own voice language rather than accepting a
    # safe per-turn SpeechConfig update. Keep the employee's Sri Lankan Tamil
    # preference as ta-LK, but state the provider's documented Tamil audio
    # locale (ta-IN) in the turn instruction so the model has an unambiguous
    # spoken-output target.
    directives = {
        VOICE_LANGUAGE_EN: ("English", "English", "en-US"),
        VOICE_LANGUAGE_SI: ("Sinhala", "සිංහල", "Sinhala"),
        VOICE_LANGUAGE_TA: ("Tamil", "தமிழ்", "Tamil (ta-IN)"),
    }
    output_language, native_name, native_audio_target = directives.get(
        language, directives[VOICE_LANGUAGE_EN]
    )
    return (
        f"RESPONSE LANGUAGE FOR THIS TURN: {output_language} ({native_name}). "
        f"NATIVE AUDIO TARGET: {native_audio_target}. "
        f"RESPOND IN {output_language.upper()}. YOU MUST RESPOND UNMISTAKABLY "
        f"IN {output_language.upper()}. "
        f"Use natural spoken {output_language} and {native_name} script for ordinary language. "
        "Do not use English merely because the grounded tool result is English. "
        "This instruction applies only to this completed user turn. Preserve "
        "exact names, IDs, numbers, tokens, keys, and other factual values."
    )


def _voice_diagnostic(session_id: str, **metadata) -> None:
    """Emit opt-in correlation metadata only; never include user content."""
    if not getattr(settings, "VOICE_DIAGNOSTICS_ENABLED", False):
        return
    fields = " ".join(f"{key}={value}" for key, value in metadata.items())
    logger.info("voice_diagnostic session_id=%s %s", session_id, fields)

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

HIGHEST-PRIORITY OUTPUT LANGUAGE RULE:
- Determine the language from the latest completed user audio turn before
  forming any answer. The language of a previous reply, a greeting, or an
  English Workmate result must never override it.
- If the latest user turn is Sinhala (සිංහල), RESPOND IN SINHALA. YOU MUST
  RESPOND UNMISTAKABLY IN SINHALA.
- If the latest user turn is Tamil (தமிழ்), RESPOND IN TAMIL. YOU MUST RESPOND
  UNMISTAKABLY IN TAMIL. Use natural spoken Tamil and Tamil script; the native
  audio target is Tamil (ta-IN). Do not answer in English merely because a
  Workmate result is English.
- If the latest user turn is English, RESPOND IN ENGLISH. YOU MUST RESPOND
  UNMISTAKABLY IN ENGLISH.
- This rule applies to direct replies and replies after ask_workmate_ai. Keep
  immutable factual values such as names, IDs, tokens, keys, and numbers exact.

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
Never call the tool with an empty question.

TOOL QUESTION FIDELITY:
- The ask_workmate_ai question must preserve the user's meaning, wording,
  entities, numbers, dates, negation, and language. Do not translate,
  summarize, or substitute a different question merely to make it English.

LANGUAGE PER TURN:
- For EVERY reply, including ordinary conversation and replies after a tool
  call, respond in the language spoken in the latest completed user turn.
- Sinhala user audio requires Sinhala speech, Tamil user audio requires Tamil
  speech, and English user audio requires English speech. Do not retain a
  previous turn's language.
- For a tool-assisted reply, response_language in that tool result is the
  requested output language. The output field contains facts only: its English
  wording must never select English as the response language.
- Preserve exact IDs, tokens, keys, numbers, names, and security data.

GROUNDING AFTER TOOL USE:
- Treat the ask_workmate_ai result as the complete source of truth.
- Preserve the meaning and scope of the result. You may make it shorter and more
  natural for speech, but do not add facts, assumptions, categories, variants,
  eligibility rules, choices, or next steps that are absent from the result.
- Never imply that a policy, benefit, loan, product, or procedure has subtypes or
  options unless the result explicitly states that it does.
- Do not ask speculative follow-up questions such as whether the user wants a
  particular kind, type, plan, or option when the result did not establish those
  alternatives.
- After giving a complete answer, stop and wait for the user. Ask a follow-up only
  when the tool result explicitly says that information is required from the user.
- If the result is ambiguous or insufficient, say that the available information
  does not specify the requested detail; do not invent a clarification premise.

PROCESSING ACKNOWLEDGEMENTS:
- While ask_workmate_ai is running, an internal message beginning
  [LUMA_FILLER_ACK] may request one short processing acknowledgement.
- Speak only the exact quoted phrase in that message. Do not call a tool,
  answer the original question, claim a result, or add any other words.
- The real answer still requires the matching ask_workmate_ai tool response."""


# Single tool — routes all questions through the full agent pipeline
WORKMATE_TOOL = {
    "name": "ask_workmate_ai",
    "behavior": "NON_BLOCKING",
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
                "description": "The user's question exactly as spoken, preserving its language, entities, numbers, dates, and negation. Do not translate or summarize it.",
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
async def get_voice_provider(user: dict = Depends(get_verified_user)):
    chat_identity(user, None)
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
async def get_realtime_token(user: dict = Depends(get_verified_user)):
    chat_identity(user, None)
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
        backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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
    active_session_user = None
    active_session_id = None

    try:
        # Browser WebSockets cannot attach a bearer header. Request the token
        # in the first message and validate it before contacting any provider.
        await websocket.send_text(json.dumps({"type": "ready"}))
        try:
            raw = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
            msg = json.loads(raw)
            if not isinstance(msg, dict) or msg.get("type") != "user_identity":
                raise HTTPException(status_code=401, detail="Sign in to use voice.")
            session_auth_token = msg.get("auth_token")
            if not isinstance(session_auth_token, str) or not session_auth_token:
                raise HTTPException(status_code=401, detail="Sign in to use voice.")
            user = await get_verified_user(HTTPAuthorizationCredentials(
                scheme="Bearer", credentials=session_auth_token,
            ))
            identity = chat_identity(user, None)
            session_email = identity.user_id
            session_name = identity.user_name or ""
            language_preference = _parse_voice_language_preference(msg.get("language_preference"))
            active_session_user = session_email
            active_session_id = uuid4().hex
            if not _claim_voice_session(active_session_user, active_session_id):
                # Do not open a second provider session for the same signed-in
                # user. This bounds accidental duplicate tabs/clicks without
                # disclosing the session that already owns the call.
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "code": "VOICE_SESSION_ACTIVE",
                    "message": "A voice call is already active for this account. End it before starting another call.",
                }))
                await websocket.close(code=1008)
                return
        except (HTTPException, asyncio.TimeoutError, ValueError) as exc:
            message = exc.detail if isinstance(exc, HTTPException) else "Sign in again to use voice."
            await websocket.send_text(json.dumps({"type": "error", "message": message}))
            await websocket.close(code=1008)
            return

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

        region = settings.LOCATION
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

        gemini_ws = await _connect_gemini_with_backoff(ws_lib, gemini_url)
        async with gemini_ws:

            logger.info("Gemini Live WebSocket connected")

            # build personalised system prompt with the user's first name
            first_name = session_name.split()[0] if session_name else "there"
            personalized_prompt = VOICE_SYSTEM_PROMPT.replace("{USER_FIRST_NAME}", first_name)

            # send setup to Gemini ONCE , after we have the name
            setup = {
                "setup": {
                    "model": model_resource,
                    "generation_config": {
                        # Output transcription is required by the browser's
                        # dynamic Luma lip-sync planner. Gemini streams it
                        # alongside native audio; it does not change the voice
                        # provider, microphone, or Workmate response pipeline.
                        # Gemini Live accepts exactly one response modality.
                        # Output-audio transcription below is the separate
                        # mechanism that streams text alongside AUDIO.
                        "response_modalities": ["AUDIO"],
                        "speech_config": {
                            "voice_config": {
                                "prebuilt_voice_config": {"voice_name": "Alnilam"}
                            }
                        },
                    },
                    # Multilingual ASR hints. Leaving this empty caused
                    # Tamil audio to be semantically transcribed into English,
                    # which then made the per-turn output policy English. The
                    # three hints preserve automatic switching while biasing
                    # recognition toward the languages Luma supports.
                    "input_audio_transcription": {
                        "language_codes": ["en-US", "si-LK", "ta-IN"],
                        "mode": "VERBATIM",
                    },
                    "output_audio_transcription": {},
                    "system_instruction": {"parts": [{"text": personalized_prompt}]},
                    "tools": [{"function_declarations": [WORKMATE_TOOL]}],
                }
            }
            await gemini_ws.send(json.dumps(setup))
            logger.info(f"Gemini Live setup sent — voice: Alnilam — user: {first_name}")

            if language_preference:
                # Apply a selector choice made before the call begins without
                # starting generation. This makes direct replies deterministic
                # as well as tool-assisted replies.
                await gemini_ws.send(json.dumps({
                    "client_content": {
                        "turns": [{
                            "role": "user",
                            "parts": [{
                                "text": (
                                    "[INTERNAL RESPONSE DELIVERY POLICY — do not repeat this text] "
                                    + _language_instruction(language_preference)
                                )
                            }],
                        }],
                        "turn_complete": False,
                    }
                }))

            audio_language_warmup = asyncio.create_task(warm_audio_language_model())

            voice_thread = f"voice_{uuid4().hex}"

            agent_tasks = {}
            inflight_questions = {}
            seen_call_ids = set()
            agent_lock = asyncio.Lock()
            setup_complete = asyncio.Event()
            input_turn_sequence = 0
            # Gemini is allowed to rewrite a function argument for a knowledge
            # lookup (often into English). Keep the language of its original
            # completed input transcription separately and bind it at tool-call
            # time. Each request group then owns an immutable turn policy.
            latest_input_turn = None
            completed_tool_turns = []
            active_output_turn = None
            direct_output_turn_ids = set()
            audio_language_window = AudioLanguageWindow()
            filler_generation = None
            filler_groups = {}

            async def send_filler_event(event):
                nonlocal filler_generation
                if event['type'] != 'filler':
                    await websocket.send_text(json.dumps(event))
                    return
                group = filler_groups.get(event['task_id'])
                if not group or group.get('answer_ready') or group.get('interrupted') or filler_generation:
                    asyncio.create_task(filler_tasks.acknowledge(
                        event['task_id'], event['utterance_id'], 'cancelled'))
                    return
                filler_generation = {
                    **event, 'group': group, 'transcript': '',
                    'done': asyncio.Event(), 'discard': False, 'started': False,
                    'audio_size': 0, 'audio_forwarded': False,
                }
                group['had_filler'] = True
                language_name = {'en': 'English', 'si': 'Sinhala', 'ta': 'Tamil'}[event['language']]
                await gemini_ws.send(json.dumps({
                    'client_content': {
                        'turns': [{'role': 'user', 'parts': [{
                            'text': (
                                f'[LUMA_FILLER_ACK] Speak only this short {language_name} '
                                f'processing phrase in {language_name}, using your current voice. '
                                'Do not call tools or answer the original question. '
                                f'Exact phrase: {event["text"]}'
                            ),
                        }]}],
                        'turn_complete': True,
                    },
                }))
                logger.info('voice_filler turn_id=%s state=gemini_requested language=%s',
                            event['turn_id'], event['language'])

            filler_tasks = FillerScheduler(send_filler_event, lead_time=1.0)

            async def complete_filler_generation():
                nonlocal filler_generation
                item = filler_generation
                if item is None:
                    return
                group = item['group']
                spoken_language = resolve_voice_language(item['transcript']) if item['transcript'] else None
                deliver = (not item['discard'] and not group.get('answer_ready')
                           and not group.get('interrupted') and item['audio_forwarded'])
                if deliver:
                    await websocket.send_text(json.dumps({
                        'type': 'filler_audio_complete', 'turn_id': item['audio_turn_id'],
                    }))
                await filler_tasks.acknowledge(
                    item['task_id'], item['utterance_id'], 'ended' if deliver else 'failed')
                logger.info('voice_filler turn_id=%s state=%s language=%s output_language=%s',
                            item['turn_id'], 'delivered' if deliver else 'discarded',
                            item['language'], spoken_language or 'unknown')
                item['done'].set()
                filler_generation = None

            async def run_agent_and_respond(call, question_key, request_group):
                call_id = call["id"]
                question = request_group["workmate_question"]
                async def start_turn_fillers():
                    # Native-script and explicit turn choices are known now.
                    # For an English ASR result, allow a short audio check to
                    # catch Tamil/Sinhala speech that Gemini translated. It
                    # must never delay Workmate or hold the first filler for
                    # the full model-loading time.
                    if (request_group['language'] == VOICE_LANGUAGE_EN
                            and request_group['language_source'] != 'user_selected_preference'):
                        try:
                            signal = await asyncio.wait_for(
                                identify_audio(request_group['audio_snapshot']), timeout=0.8)
                            if signal['language']:
                                request_group['language'] = {
                                    'en': VOICE_LANGUAGE_EN, 'si': VOICE_LANGUAGE_SI,
                                    'ta': VOICE_LANGUAGE_TA,
                                }[signal['language']]
                        except asyncio.TimeoutError:
                            logger.info('voice_filler turn_id=%s audio_check=timed_out',
                                        request_group['turn_id'])
                    request_group['audio_snapshot'] = b''
                    if request_group.get('answer_ready') or request_group.get('interrupted'):
                        return
                    language = request_group['language'].split('-')[0]
                    await filler_tasks.start(call_id, request_group['turn_id'], language,
                                             started=request_group['started'])
                    logger.info('voice_filler turn_id=%s state=started language=%s source=%s',
                                request_group['turn_id'], language, request_group['language_source'])

                filler_start_task = asyncio.create_task(start_turn_fillers())

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
                        request_group['answer_ready'] = True
                        if not filler_start_task.done():
                            try:
                                await asyncio.wait_for(asyncio.shield(filler_start_task), timeout=0.1)
                            except asyncio.TimeoutError:
                                filler_start_task.cancel()
                        await filler_tasks.stop(call_id, 'answer_ready')
                        if request_group.get('had_filler'):
                            await websocket.send_text(json.dumps({
                                'type': 'stop_filler_audio', 'turn_id': request_group['turn_id'],
                            }))
                        if filler_generation and filler_generation['task_id'] == call_id:
                            filler_generation['discard'] = True
                            try:
                                await asyncio.wait_for(filler_generation['done'].wait(), timeout=4)
                            except asyncio.TimeoutError:
                                logger.warning('voice_filler turn_id=%s state=provider_timeout',
                                               request_group['turn_id'])
                        language = request_group["language"]
                        _voice_diagnostic(
                            voice_thread,
                            event="workmate_response",
                            turn_id=request_group["turn_id"],
                            language=language,
                            response_status="received",
                        )
                        logger.info("Voice tool result: id=%s, language=%s, %d chars", call_id, language, len(answer))
                        # Gemini receives this with the grounded Workmate
                        # answer, avoiding a second translation request or a
                        # session reconnect for a language switch.
                        result = {
                            "output": answer,
                            "response_language": language,
                            "instruction": _language_instruction(language),
                        }

                    # Gemini can emit the same question again under a new call
                    # ID while the first lookup is pending. Complete every call
                    # together so the provider sees one resolved tool batch,
                    # while Workmate itself executes only once.
                    function_responses = [{
                        "id": grouped_call["id"],
                        "name": grouped_call["name"],
                        "response": result,
                        **({"scheduling": "INTERRUPT"} if request_group.get('had_filler') else {}),
                    } for grouped_call in request_group["calls"]]
                    # Function-response JSON metadata alone is not a strong
                    # enough language control for native audio. Deliver the
                    # same immutable turn policy as current session context
                    # immediately before the function response. Keeping this
                    # incremental content turn incomplete prevents it from
                    # starting a second response or replaying the tool call;
                    # the following tool_response resumes the pending call.
                    language = request_group["language"]
                    await gemini_ws.send(json.dumps({
                        "client_content": {
                            "turns": [{
                                "role": "user",
                                "parts": [{
                                    "text": (
                                        "[INTERNAL RESPONSE DELIVERY POLICY — do not repeat this text] "
                                        + _language_instruction(language)
                                    )
                                }],
                            }],
                            "turn_complete": False,
                        }
                    }))
                    await gemini_ws.send(json.dumps({
                        "tool_response": {"function_responses": function_responses}
                    }))
                    completed_tool_turns.append(request_group)
                    logger.info(
                        "voice_language turn_id=%s tool_response_language_policy=%s",
                        request_group["turn_id"], request_group["language"],
                    )
                except asyncio.CancelledError:
                    # Cancelled calls must not receive empty or stale results.
                    logger.info("Voice tool cancelled: id=%s", call_id)
                    raise
                except Exception:
                    logger.exception("Voice tool response failed: id=%s", call_id)
                finally:
                    request_group['answer_ready'] = True
                    filler_start_task.cancel()
                    await asyncio.gather(filler_start_task, return_exceptions=True)
                    request_group['audio_snapshot'] = b''
                    await filler_tasks.stop(call_id, 'cancelled')
                    filler_tasks.forget(call_id)
                    for grouped_call in request_group["calls"]:
                        agent_tasks.pop(grouped_call["id"], None)
                    if inflight_questions.get(question_key) is request_group:
                        inflight_questions.pop(question_key, None)
                    filler_groups.pop(call_id, None)

            async def browser_to_gemini():
                nonlocal language_preference
                loop = asyncio.get_running_loop()
                stream_start_time = None
                audio_seconds_sent = 0.0

                try:
                    while True:
                        raw = await websocket.receive_text()
                        msg = json.loads(raw)
                        if msg.get("type") == "audio":
                            if audio_language_window.feed(msg.get('data')):
                                for group in inflight_questions.values():
                                    group['interrupted'] = True
                                await filler_tasks.stop_all('user_interrupt')
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
                        elif msg.get('type') == 'user_interrupt':
                            for group in inflight_questions.values():
                                group['interrupted'] = True
                            await filler_tasks.stop_all('user_interrupt')
                        elif msg.get("type") == "audio_playback_started":
                            # The client reports only the server-assigned turn
                            # identifier, never transcript or audio content.
                            playback_turn_id = msg.get("turn_id")
                            if isinstance(playback_turn_id, str):
                                _voice_diagnostic(
                                    voice_thread,
                                    event="browser_audio_playback_started",
                                    turn_id=playback_turn_id,
                                    browser_playback_turn_id=playback_turn_id,
                                )
                        elif msg.get("type") == "language_preference":
                            language_preference = _parse_voice_language_preference(msg.get("language"))
                            if language_preference:
                                # This is a deterministic user choice, sent
                                # before their next utterance. It adds context
                                # without completing a model turn.
                                await gemini_ws.send(json.dumps({
                                    "client_content": {
                                        "turns": [{
                                            "role": "user",
                                            "parts": [{
                                                "text": (
                                                    "[INTERNAL RESPONSE DELIVERY POLICY — do not repeat this text] "
                                                    + _language_instruction(language_preference)
                                                )
                                            }],
                                        }],
                                        "turn_complete": False,
                                    }
                                }))
                            _voice_diagnostic(
                                voice_thread,
                                event="language_preference_updated",
                                language=language_preference or "auto",
                            )
                except WebSocketDisconnect:
                    logger.info("Browser WebSocket disconnected")
                except Exception as e:
                    logger.error(f"browser_to_gemini error: {e}")

            async def gemini_to_browser():
                nonlocal input_turn_sequence, latest_input_turn, active_output_turn

                def begin_output_turn():
                    """Bind provider output/audio without influencing synthesis."""
                    nonlocal active_output_turn
                    if active_output_turn is not None:
                        return active_output_turn
                    if completed_tool_turns:
                        active_output_turn = completed_tool_turns.pop(0)
                    # A direct answer has no function response.  Only use the
                    # latest input turn when no function call is outstanding;
                    # that prevents a pre-tool event from claiming a later
                    # tool-assisted answer.
                    elif latest_input_turn and not agent_tasks:
                        turn_id = latest_input_turn["turn_id"]
                        if turn_id in direct_output_turn_ids:
                            return None
                        direct_output_turn_ids.add(turn_id)
                        active_output_turn = dict(latest_input_turn)
                        active_output_turn["source"] = "direct_user_turn"
                    else:
                        return None
                    if language_preference:
                        active_output_turn["language"] = language_preference
                        active_output_turn["source"] = "user_selected_preference"
                    requested_language = active_output_turn["language"]
                    logger.info(
                        "voice_language turn_id=%s requested_output_language=%s",
                        active_output_turn["turn_id"], requested_language,
                    )
                    _voice_diagnostic(
                        voice_thread,
                        event="gemini_response_started",
                        turn_id=active_output_turn["turn_id"],
                        detected_language=active_output_turn["language"],
                        requested_output_language=requested_language,
                    )
                    return active_output_turn

                try:
                    async for raw_msg in gemini_ws:
                        data = json.loads(raw_msg)
                        if "setupComplete" in data:
                            setup_complete.set()
                            await websocket.send_text(json.dumps({"type": "connected"}))
                        server_content = data.get("serverContent", {})

                        if server_content.get("interrupted"):
                            if filler_generation:
                                filler_generation['discard'] = True
                                await complete_filler_generation()
                            await filler_tasks.stop_all('user_interrupt')
                            await websocket.send_text(json.dumps({"type": "stop_audio"}))

                        for call_id in data.get("toolCallCancellation", {}).get("ids", []):
                            task = agent_tasks.get(call_id)
                            if task is not None:
                                task.cancel()

                        # The same Gemini session speaks deterministic filler
                        # text while the Workmate call runs. Buffer that short
                        # model turn until it is complete so late filler audio
                        # cannot be confused with the grounded final answer.
                        if filler_generation:
                            parts = server_content.get('modelTurn', {}).get('parts', [])
                            filler_text = server_content.get('outputTranscription', {}).get('text', '')
                            if parts or filler_text:
                                filler_generation['started'] = True
                                for part in parts:
                                    if 'inlineData' in part:
                                        chunk = part['inlineData'].get('data', '')
                                        filler_generation['audio_size'] += len(chunk)
                                        if filler_generation['audio_size'] > 512000:
                                            filler_generation['discard'] = True
                                        if (chunk and not filler_generation['discard']
                                                and not filler_generation['group'].get('answer_ready')
                                                and not filler_generation['group'].get('interrupted')):
                                            if not filler_generation['audio_forwarded']:
                                                filler_generation['audio_forwarded'] = True
                                                filler_generation['audio_turn_id'] = (
                                                    f"{filler_generation['turn_id']}_filler_"
                                                    f"{filler_generation['utterance_id'].rsplit(':', 1)[-1]}")
                                                await websocket.send_text(json.dumps({
                                                    'type': 'filler_audio_started',
                                                    'turn_id': filler_generation['audio_turn_id'],
                                                    'language': filler_generation['language'],
                                                    'text': filler_generation['text'],
                                                }))
                                            await websocket.send_text(json.dumps({
                                                'type': 'filler_audio', 'data': chunk,
                                                'turn_id': filler_generation['audio_turn_id'],
                                                'language': filler_generation['language'],
                                            }))
                                    elif 'text' in part and not filler_text:
                                        filler_generation['transcript'] += part['text']
                                filler_generation['transcript'] += filler_text
                                server_content = {**server_content, 'modelTurn': {}, 'outputTranscription': {}}
                            if server_content.get('turnComplete') and filler_generation['started']:
                                await complete_filler_generation()
                                server_content = {**server_content, 'turnComplete': False}

                        # Prefer output-audio transcription when available;
                        # otherwise retain modelTurn text as the compatible
                        # fallback. Forwarding both duplicates the response.
                        output_text = server_content.get("outputTranscription", {}).get("text", "")

                        # forward audio and transcript to browser
                        for part in server_content.get("modelTurn", {}).get("parts", []):
                            if "inlineData" in part:
                                output_turn = begin_output_turn()
                                inline = part["inlineData"]
                                if output_turn:
                                    _voice_diagnostic(
                                        voice_thread,
                                        event="audio_playback_forwarded",
                                        turn_id=output_turn["turn_id"],
                                        language=output_turn["language"],
                                        audio_playback_turn_id=output_turn["turn_id"],
                                    )
                                await websocket.send_text(json.dumps({
                                    "type":      "audio",
                                    "data":      inline.get("data", ""),
                                    "mime_type": inline.get("mimeType", "audio/pcm;rate=24000"),
                                    "turn_id": output_turn["turn_id"] if output_turn else None,
                                    "requested_output_language": output_turn["language"] if output_turn else None,
                                }))
                            elif "text" in part and not output_text:
                                output_turn = begin_output_turn()
                                await websocket.send_text(json.dumps({
                                    "type": "transcript",
                                    "role": "model",
                                    "text": part["text"],
                                    "turn_id": output_turn["turn_id"] if output_turn else None,
                                    "requested_output_language": output_turn["language"] if output_turn else None,
                                }))

                        input_text = server_content.get("inputTranscription", {}).get("text", "")
                        if input_text:
                            input_turn_sequence += 1
                            latest_input_turn = {
                                "turn_id": f"voice_turn_{input_turn_sequence}",
                                "language": resolve_voice_language(input_text),
                                "source": "gemini_input_transcription",
                                # Kept only in-memory for the active turn. It
                                # is never logged or persisted by diagnostics.
                                "question": input_text,
                            }
                            logger.info(
                                "voice_language turn_id=%s detected_language=%s language_source=%s",
                                latest_input_turn["turn_id"], latest_input_turn["language"], latest_input_turn["source"],
                            )
                            _voice_diagnostic(
                                voice_thread,
                                event="input_transcription",
                                turn_id=latest_input_turn["turn_id"],
                                language=latest_input_turn["language"],
                                finality="provider_completed",
                            )
                            await websocket.send_text(json.dumps({
                                "type": "transcript",
                                "role": "user",
                                "text": input_text,
                                "language": latest_input_turn["language"],
                            }))

                        # Native-audio sessions emit spoken output through
                        # outputTranscription rather than modelTurn text. Send
                        # it through the same browser transcript path so Luma
                        # receives real response text while PCM is playing.
                        if output_text:
                            output_turn = begin_output_turn()
                            generated_language = resolve_voice_language(output_text)
                            if output_turn:
                                logger.info(
                                    "voice_language turn_id=%s gemini_output_transcription_language=%s",
                                    output_turn["turn_id"], generated_language,
                                )
                                _voice_diagnostic(
                                    voice_thread,
                                    event="output_transcription",
                                    turn_id=output_turn["turn_id"],
                                    requested_output_language=output_turn["language"],
                                    gemini_output_transcription_language=generated_language,
                                )
                            await websocket.send_text(json.dumps({
                                "type": "transcript",
                                "role": "model",
                                "text": output_text,
                                "turn_id": output_turn["turn_id"] if output_turn else None,
                                "requested_output_language": output_turn["language"] if output_turn else None,
                                "gemini_output_transcription_language": generated_language,
                            }))
                            active_output_turn = None

                        if server_content.get("turnComplete"):
                            await websocket.send_text(json.dumps({"type": "turn_complete"}))
                            active_output_turn = None

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
                            if filler_generation and not filler_generation['group'].get('interrupted'):
                                group = filler_generation['group']
                                group['calls'].append(call)
                                agent_tasks[call_id] = group['task']
                                logger.info('Coalescing tool call during filler: id=%s', call_id)
                                continue
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
                            # Input transcription normally represents the
                            # user's turn, but can arrive late or be normalized
                            # by the provider. An unambiguous native script in
                            # the function argument is therefore stronger
                            # evidence than an English transcript. Workmate's
                            # result is never used for this decision.
                            native_tool_language = _native_script_language(question)
                            input_question = (latest_input_turn or {}).get("question", "")
                            input_native_language = _native_script_language(input_question)
                            if native_tool_language:
                                if latest_input_turn is None:
                                    input_turn_sequence += 1
                                    tool_turn = {"turn_id": f"voice_turn_{input_turn_sequence}"}
                                else:
                                    tool_turn = dict(latest_input_turn)
                                tool_turn.update({
                                    "language": native_tool_language,
                                    "source": "tool_argument_native_script",
                                    "workmate_question": question,
                                })
                            elif input_native_language or (latest_input_turn and latest_input_turn["language"] != VOICE_LANGUAGE_EN):
                                tool_turn = dict(latest_input_turn)
                                tool_turn.update({
                                    "language": input_native_language or latest_input_turn["language"],
                                    "source": "input_transcription_preserved",
                                    "workmate_question": input_question,
                                })
                            elif latest_input_turn is None:
                                input_turn_sequence += 1
                                tool_turn = {
                                    "turn_id": f"voice_turn_{input_turn_sequence}",
                                    "language": resolve_voice_language(question),
                                    "source": "tool_argument_fallback",
                                    "workmate_question": question,
                                }
                            else:
                                tool_turn = dict(latest_input_turn)
                                tool_turn["workmate_question"] = question
                            if language_preference:
                                tool_turn["language"] = language_preference
                                tool_turn["source"] = "user_selected_preference"
                            logger.info(
                                "voice_language turn_id=%s tool_call_language=%s language_source=%s",
                                tool_turn["turn_id"], tool_turn["language"], tool_turn["source"],
                            )
                            _voice_diagnostic(
                                voice_thread,
                                event="tool_call",
                                turn_id=tool_turn["turn_id"],
                                language=tool_turn["language"],
                                tool_call_id=call_id,
                                question_source=tool_turn["source"],
                            )
                            request_group = {
                                "calls": [call],
                                "task": None,
                                "turn_id": tool_turn["turn_id"],
                                "language": tool_turn["language"],
                                "language_source": tool_turn["source"],
                                "workmate_question": tool_turn["workmate_question"],
                                "audio_snapshot": audio_language_window.snapshot(),
                                "started": asyncio.get_running_loop().time(),
                            }
                            filler_groups[call_id] = request_group
                            task = asyncio.create_task(
                                run_agent_and_respond(call, question_key, request_group)
                            )
                            request_group["task"] = task
                            inflight_questions[question_key] = request_group
                            agent_tasks[call_id] = task

                except Exception as e:
                    logger.error("gemini_to_browser error: %s", type(e).__name__)
                    try:
                        await websocket.send_text(json.dumps(_voice_error_payload(e)))
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
                # A coalesced tool group is referenced by several call IDs;
                # cancel and await each task once during session teardown.
                pending = set(agent_tasks.values())
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                await filler_tasks.stop_all('cancelled')
                audio_language_warmup.cancel()
                await asyncio.gather(audio_language_warmup, return_exceptions=True)
                audio_language_window.clear()

    except Exception as e:
        logger.error("Gemini voice proxy error: %s", type(e).__name__)
        try:
            await websocket.send_text(json.dumps(_voice_error_payload(e)))
        except Exception:
            pass
    finally:
        _release_voice_session(active_session_user, active_session_id)
        logger.info("Gemini Live voice session ended")
        try:
            await websocket.send_text(json.dumps({"type": "session_end"}))
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass
