# (Gemini WebSocket proxy — the biggest chunk)
import asyncio
import json
import logging
import os

from fastapi import WebSocket, WebSocketDisconnect

from core.config import settings
from domain.tools.rag_tools import _search_qdrant_knowledge_base
from .session import _resolve_session
from .tools import (
    KB_TOOL_NAME, KB_TOOL_DESCRIPTION, KB_TOOL_PARAMETERS,
    LEAVE_TOOL_NAME, LEAVE_TOOL_DESCRIPTION, LEAVE_TOOL_PARAMETERS,
    VOICE_SYSTEM_PROMPT, GEMINI_LIVE_MODEL,
)
from domain.tools.api_tools import fetch_leave_balance_for_user

logger = logging.getLogger(__name__)


def _get_vertex_access_token() -> str:
    import google.auth
    import google.auth.transport.requests

    cred_path = settings.GOOGLE_APPLICATION_CREDENTIALS
    if not cred_path:
        raise RuntimeError("GOOGLE_APPLICATION_CREDENTIALS not set in .env")

    if not os.path.isabs(cred_path):
        backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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


async def gemini_voice_proxy(websocket: WebSocket):
    await websocket.accept()
    logger.info("Browser WebSocket connected — starting Gemini Live proxy")

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
        location = settings.LOCATION or "us-central1"

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

            # wait for auth message from browser right after ready
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
                                    "type": "audio",
                                    "data": inline.get("data", ""),
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
                                query = args.get("query", "")
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