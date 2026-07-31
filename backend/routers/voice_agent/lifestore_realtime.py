"""
LifeStore realtime voice router.

This skeleton is intentionally separate from the Workmate realtime router so
the existing /voice experience stays untouched while LifeStore voice is built.
Later steps will connect this route to the existing LifeStore chat endpoint.
"""

import logging
import asyncio
import json
import os
import re
import httpx

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from core.config import settings
from routers.chat import chat as chat_endpoint
from schemas.chat import ChatRequest

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/realtime/lifestore",
    tags=["lifestore-realtime"],
)

OPENAI_REALTIME_MODEL = "gpt-realtime-2"

GEMINI_LIVE_MODEL = "gemini-live-2.5-flash-preview-native-audio-09-2025"

LIFESTORE_VOICE_PROMPT = """You are Ask LifeStore, a live voice assistant for SLTMobitel LifeStore customers.
For every LifeStore product, category, availability, comparison, cart, or checkout request, call the ask_lifestore_chat function.
Do not answer product facts, prices, stock status, seller names, cart totals, or checkout details from memory.
Keep spoken replies short and natural. Do not use markdown tables in voice replies.
Never speak raw JSON, hidden product-card metadata, checkout tokens, URLs, or implementation markers.
When checkout is prepared, tell the customer the checkout is ready on screen and that this is a sandbox demo if the chat result says so.
When the checkout form is visible, collect first name, last name, email, and phone number one at a time.
After each customer answer, call set_checkout_field with the field and cleaned value so the visible form updates.
If the customer corrects a value, call set_checkout_field again for that field.
Before payment, call get_checkout_form_state, read back the collected details briefly, and ask for confirmation.
Only after the customer clearly confirms, call start_checkout_payment.
If the customer asks to close or hide the checkout card, call close_checkout.
If the customer asks to show the checkout card again, call show_checkout.
If the customer asks to clear, close, or hide the product cards, call clear_product_cards."""

ASK_LIFESTORE_TOOL_NAME = "ask_lifestore_chat"
ASK_LIFESTORE_TOOL_DESCRIPTION = (
    "Send the user's LifeStore request to the existing Ask LifeStore chat agent. "
    "Use this for all product search, product details, availability, comparisons, "
    "cart changes, and checkout requests."
)
ASK_LIFESTORE_TOOL_PARAMETERS = {
    "type": "object",
    "properties": {
        "message": {
            "type": "string",
            "description": "The user's LifeStore request as plain text.",
        },
    },
    "required": ["message"],
}

CHECKOUT_FIELD_TOOL_NAMES = {
    "set_checkout_field",
    "get_checkout_form_state",
    "start_checkout_payment",
    "clear_product_cards",
    "close_checkout",
    "show_checkout",
}

SET_CHECKOUT_FIELD_TOOL_PARAMETERS = {
    "type": "object",
    "properties": {
        "field": {
            "type": "string",
            "enum": ["first_name", "last_name", "email", "phone"],
        },
        "value": {"type": "string"},
    },
    "required": ["field", "value"],
}

EMPTY_TOOL_PARAMETERS = {
    "type": "object",
    "properties": {},
}

PRODUCT_CARDS_START = "[LIFESTORE_PRODUCT_CARDS]"
PRODUCT_CARDS_END = "[/LIFESTORE_PRODUCT_CARDS]"
CHECKOUT_TOKEN_RE = re.compile(r"\[RENDER_LIFESTORE_CHECKOUT:([^\]\s]+)\]")


class LifestoreChatToolRequest(BaseModel):
    message: str
    thread_id: str
    user_id: str = "anonymous"


class LifestoreChatToolResponse(BaseModel):
    answer: str
    events: list[dict] = Field(default_factory=list)


def _active_provider() -> str:
    """Return the configured realtime provider for LifeStore voice."""
    if settings.GOOGLE_APPLICATION_CREDENTIALS:
        return "gemini"
    if settings.OPENAI_API_KEY:
        return "openai"
    return "none"


async def _streaming_response_text(response: StreamingResponse) -> str:
    """Collect the existing chat endpoint's streamed answer into one string."""
    chunks: list[str] = []

    async for chunk in response.body_iterator:
        if isinstance(chunk, bytes):
            chunks.append(chunk.decode("utf-8", errors="replace"))
        else:
            chunks.append(str(chunk))

    return "".join(chunks).strip()


def _extract_lifestore_events(text: str) -> tuple[str, list[dict]]:
    """Remove LifeStore UI markers from visible text and return UI events."""
    clean_text = text or ""
    events: list[dict] = []

    while True:
        start_idx = clean_text.find(PRODUCT_CARDS_START)
        if start_idx < 0:
            break

        payload_start = start_idx + len(PRODUCT_CARDS_START)
        end_idx = clean_text.find(PRODUCT_CARDS_END, payload_start)
        if end_idx < 0:
            clean_text = clean_text[:start_idx].rstrip()
            break

        payload = clean_text[payload_start:end_idx].strip()
        try:
            parsed = json.loads(payload)
            if isinstance(parsed, dict):
                products = parsed.get("products")
                if isinstance(products, list) and products:
                    events.append({
                        "type": "product_cards",
                        "display": parsed.get("display") or "carousel",
                        "products": products,
                    })
        except Exception:
            logger.warning("Failed to parse LifeStore product-card metadata")

        clean_text = (
            clean_text[:start_idx]
            + clean_text[end_idx + len(PRODUCT_CARDS_END):]
        )

    checkout_ids = CHECKOUT_TOKEN_RE.findall(clean_text)
    for order_id in checkout_ids:
        events.append({"type": "checkout", "order_id": order_id})

    clean_text = CHECKOUT_TOKEN_RE.sub("", clean_text)
    clean_text = re.sub(r"\n{3,}", "\n\n", clean_text).strip()

    return clean_text, events


async def _ask_lifestore_chat(
    *,
    message: str,
    thread_id: str,
    user_id: str = "anonymous",
) -> LifestoreChatToolResponse:
    """Call the existing LifeStore chat agent and return voice-safe output."""
    chat_request = ChatRequest(
        agent_id="lifestore",
        message=message,
        thread_id=thread_id,
        user_id=(user_id or "anonymous").strip() or "anonymous",
    )

    response = await chat_endpoint(chat_request)
    raw_answer = await _streaming_response_text(response)
    answer, events = _extract_lifestore_events(raw_answer)
    return LifestoreChatToolResponse(answer=answer, events=events)


def _get_vertex_access_token() -> str:
    """Return a short-lived Vertex AI access token for Gemini Live."""
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


@router.get("/provider")
async def get_lifestore_voice_provider():
    """
    Return the active voice provider.

    This mirrors the Workmate voice provider endpoint but stays under the
    LifeStore prefix so frontend changes can be isolated.
    """
    provider = _active_provider()
    if provider == "none":
        raise HTTPException(
            status_code=500,
            detail=(
                "No LifeStore voice provider configured. Set "
                "GOOGLE_APPLICATION_CREDENTIALS or OPENAI_API_KEY in .env."
            ),
        )

    model = GEMINI_LIVE_MODEL if provider == "gemini" else OPENAI_REALTIME_MODEL
    logger.info("LifeStore voice provider: %s / %s", provider, model)
    return {"provider": provider, "model": model}


@router.get("/token")
async def get_lifestore_realtime_token():
    """
    Generate an OpenAI ephemeral token for the LifeStore voice page.

    LifeStore-specific tools are not attached in this step; this only proves
    the isolated endpoint path works without touching the Workmate voice route.
    """
    if not settings.OPENAI_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="OPENAI_API_KEY is not configured.",
        )

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                "https://api.openai.com/v1/realtime/client_secrets",
                headers={
                    "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                    "OpenAI-Safety-Identifier": "lifestore-ai-voice",
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
            logger.error(
                "LifeStore OpenAI token error: %s | %s",
                response.status_code,
                response.text,
            )
            raise HTTPException(
                status_code=502,
                detail=f"Failed to create OpenAI session: {response.text}",
            )

        return response.json()

    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Timeout connecting to OpenAI")
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("LifeStore OpenAI token error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.websocket("/ws/voice")
async def lifestore_voice_proxy(websocket: WebSocket):
    await websocket.accept()
    logger.info("LifeStore voice WebSocket connected; starting Gemini Live proxy")

    session_user_id = "anonymous"
    voice_thread_id = ""

    try:
        import websockets as ws_lib

        try:
            access_token = _get_vertex_access_token()
        except Exception as exc:
            await websocket.send_json({
                "type": "error",
                "message": f"Vertex AI auth failed: {exc}",
            })
            return

        project_id = settings.PROJECT_ID
        region = settings.LOCATION or "us-central1"
        if not project_id:
            await websocket.send_json({
                "type": "error",
                "message": "PROJECT_ID not set in .env",
            })
            return

        host = f"{region}-aiplatform.googleapis.com"
        path = "google.cloud.aiplatform.v1.LlmBidiService/BidiGenerateContent"
        gemini_url = f"wss://{host}/ws/{path}?access_token={access_token}"
        model_resource = (
            f"projects/{project_id}/locations/{region}"
            f"/publishers/google/models/{GEMINI_LIVE_MODEL}"
        )

        async with ws_lib.connect(
            gemini_url,
            additional_headers={"Content-Type": "application/json"},
            max_size=10 * 1024 * 1024,
        ) as gemini_ws:
            setup = {
                "setup": {
                    "model": model_resource,
                    "generation_config": {
                        "response_modalities": ["AUDIO"],
                        "speech_config": {
                            "voice_config": {
                                "prebuilt_voice_config": {
                                    "voice_name": "Aoede",
                                }
                            }
                        },
                    },
                    "system_instruction": {
                        "parts": [{"text": LIFESTORE_VOICE_PROMPT}]
                    },
                    "tools": [
                        {
                            "function_declarations": [
                                {
                                    "name": ASK_LIFESTORE_TOOL_NAME,
                                    "description": ASK_LIFESTORE_TOOL_DESCRIPTION,
                                    "parameters": ASK_LIFESTORE_TOOL_PARAMETERS,
                                },
                                {
                                    "name": "set_checkout_field",
                                    "description": "Update one visible LifeStore checkout form field from the customer voice answer.",
                                    "parameters": SET_CHECKOUT_FIELD_TOOL_PARAMETERS,
                                },
                                {
                                    "name": "get_checkout_form_state",
                                    "description": "Read the current LifeStore checkout form values and missing fields before confirmation.",
                                    "parameters": EMPTY_TOOL_PARAMETERS,
                                },
                                {
                                    "name": "start_checkout_payment",
                                    "description": "Start the PayHere sandbox checkout after the customer confirms all checkout details are correct.",
                                    "parameters": EMPTY_TOOL_PARAMETERS,
                                },
                                {
                                    "name": "clear_product_cards",
                                    "description": "Clear or hide the visible LifeStore product cards panel.",
                                    "parameters": EMPTY_TOOL_PARAMETERS,
                                },
                                {
                                    "name": "close_checkout",
                                    "description": "Close or hide the visible LifeStore checkout card without canceling the cart.",
                                    "parameters": EMPTY_TOOL_PARAMETERS,
                                },
                                {
                                    "name": "show_checkout",
                                    "description": "Show the latest LifeStore checkout card again after it was closed.",
                                    "parameters": EMPTY_TOOL_PARAMETERS,
                                },
                            ]
                        }
                    ],
                }
            }
            await gemini_ws.send(json.dumps(setup))
            await websocket.send_json({
                "type": "ready",
                "message": "LifeStore voice backend ready",
            })

            try:
                raw_auth = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
                auth_msg = json.loads(raw_auth)
                if auth_msg.get("type") == "auth":
                    session_user_id = (
                        auth_msg.get("user_id")
                        or auth_msg.get("session_token")
                        or "anonymous"
                    )
                    voice_thread_id = str(auth_msg.get("thread_id") or "").strip()
            except asyncio.TimeoutError:
                logger.warning("LifeStore voice auth message not received")
            except Exception as exc:
                logger.warning("LifeStore voice auth message error: %s", exc)

            if not voice_thread_id:
                voice_thread_id = "lifestore-voice-anonymous"

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
                        elif msg.get("type") == "checkout_tool_result":
                            response_payload = {
                                "name": msg.get("tool_name") or "",
                                "response": {
                                    "output": msg.get("result") or {},
                                },
                            }
                            if msg.get("call_id"):
                                response_payload["id"] = msg.get("call_id")

                            await gemini_ws.send(json.dumps({
                                "tool_response": {
                                    "function_responses": [response_payload]
                                }
                            }))
                        elif msg.get("type") == "end":
                            break
                except WebSocketDisconnect:
                    logger.info("LifeStore browser WebSocket disconnected")
                except Exception as exc:
                    logger.error("LifeStore browser_to_gemini error: %s", exc)

            async def gemini_to_browser():
                try:
                    async for raw_msg in gemini_ws:
                        data = json.loads(raw_msg)
                        server_content = data.get("serverContent", {})

                        parts = server_content.get("modelTurn", {}).get("parts", [])
                        for part in parts:
                            if "inlineData" in part:
                                inline = part["inlineData"]
                                await websocket.send_json({
                                    "type": "audio",
                                    "data": inline.get("data", ""),
                                    "mime_type": inline.get("mimeType", "audio/pcm;rate=24000"),
                                })
                            elif "text" in part:
                                await websocket.send_json({
                                    "type": "transcript",
                                    "role": "model",
                                    "text": part["text"],
                                })

                        input_text = server_content.get("inputTranscription", {}).get("text", "")
                        if input_text:
                            await websocket.send_json({
                                "type": "transcript",
                                "role": "user",
                                "text": input_text,
                            })

                        if server_content.get("turnComplete"):
                            await websocket.send_json({"type": "turn_complete"})

                        tool_calls = data.get("toolCall", {}).get("functionCalls", [])
                        for call in tool_calls:
                            tool_name = call.get("name")
                            if tool_name in CHECKOUT_FIELD_TOOL_NAMES:
                                await websocket.send_json({
                                    "type": "checkout_tool_call",
                                    "tool_name": tool_name,
                                    "call_id": call.get("id") or "",
                                    "args": call.get("args") or {},
                                })
                                continue

                            if tool_name != ASK_LIFESTORE_TOOL_NAME:
                                continue

                            args = call.get("args") or {}
                            message = (
                                args.get("message")
                                or args.get("query")
                                or args.get("question")
                                or ""
                            )
                            result = await _ask_lifestore_chat(
                                message=str(message),
                                thread_id=voice_thread_id,
                                user_id=session_user_id,
                            )

                            for event in result.events:
                                await websocket.send_json(event)

                            await gemini_ws.send(json.dumps({
                                "tool_response": {
                                    "function_responses": [
                                        {
                                            "name": ASK_LIFESTORE_TOOL_NAME,
                                            "response": {
                                                "output": result.answer or "I could not find an answer.",
                                            },
                                        }
                                    ]
                                }
                            }))
                except Exception as exc:
                    logger.error("LifeStore gemini_to_browser error: %s", exc)
                    try:
                        await websocket.send_json({"type": "error", "message": str(exc)})
                    except Exception:
                        pass

            await asyncio.gather(browser_to_gemini(), gemini_to_browser())

    except WebSocketDisconnect:
        logger.info("LifeStore voice WebSocket disconnected")
    except Exception as exc:
        logger.error("LifeStore voice WebSocket error: %s", exc)
        try:
            await websocket.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass
    finally:
        try:
            await websocket.send_json({"type": "session_end"})
        except Exception:
            pass


@router.post("/chat-tool", response_model=LifestoreChatToolResponse)
async def lifestore_chat_tool(request: LifestoreChatToolRequest):
    """
    Bridge a LifeStore voice utterance into the existing LifeStore chat agent.

    This intentionally reuses the /api/v1/chat endpoint implementation with
    agent_id="lifestore" instead of duplicating LifeStore product/cart logic in
    the realtime router.
    """
    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    thread_id = request.thread_id.strip()
    if not thread_id:
        raise HTTPException(status_code=400, detail="thread_id is required.")

    try:
        return await _ask_lifestore_chat(
            message=message,
            thread_id=thread_id,
            user_id=request.user_id,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("LifeStore chat-tool failed")
        raise HTTPException(
            status_code=502,
            detail=f"LifeStore chat agent failed: {exc}",
        )
