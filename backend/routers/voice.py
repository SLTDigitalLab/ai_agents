"""
voice.py  —  Voice feature router for Workmate AI
Provides two endpoints:
  POST /api/v1/voice/stt  — audio file → text  (OpenAI Whisper)
  POST /api/v1/voice/tts  — text → audio file  (OpenAI TTS)

Uses the same OPENAI_API_KEY already configured in the project.
No new environment variables required.
"""

import io
import logging
from fastapi import APIRouter, UploadFile, File, HTTPException, Form
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI
from core.config import settings          # existing settings — has settings.OPENAI_API_KEY

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/voice", tags=["voice"])

# Reuse the same OpenAI client pattern used elsewhere in the project
client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


# ── STT  ────────────────────────────────────────────────────────────────────
@router.post("/stt")
async def speech_to_text(
    audio: UploadFile = File(...),
    language: str = Form(default="en"),   # "en" | "si" | "ta"  — Whisper supports all
):
    """
    Receives an audio file (webm/mp4/wav) from the browser,
    sends it to OpenAI Whisper, and returns the transcribed text.
    """
    try:
        # Read the uploaded audio bytes
        audio_bytes = await audio.read()

        if len(audio_bytes) == 0:
            raise HTTPException(status_code=400, detail="Empty audio file received")

        # Whisper needs a file-like object with a filename so it knows the format
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = audio.filename or "recording.webm"

        # Call Whisper API
        transcript = await client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            language=language,            # pass language hint for accuracy
        )

        logger.info(f"STT transcription successful: '{transcript.text[:60]}...'")
        return {"text": transcript.text}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"STT error: {e}")
        raise HTTPException(status_code=500, detail=f"Speech-to-text failed: {str(e)}")


# ── TTS  ────────────────────────────────────────────────────────────────────
@router.post("/tts")
async def text_to_speech(
    text: str = Form(...),
    voice: str = Form(default="alloy"),   # alloy | echo | fable | onyx | nova | shimmer
):
    """
    Receives a text string from the frontend,
    sends it to OpenAI TTS, and streams back the MP3 audio.
    """
    try:
        if not text or not text.strip():
            raise HTTPException(status_code=400, detail="Empty text received")

        # Truncate very long responses — TTS has a 4096 char limit
        text_to_speak = text.strip()[:4096]

        # Call OpenAI TTS API
        response = await client.audio.speech.create(
            model="tts-1",                # tts-1 is fast; tts-1-hd for higher quality
            voice=voice,
            input=text_to_speak,
            response_format="mp3",
        )

        # Stream the audio bytes back to the browser
        audio_bytes = response.content

        logger.info(f"TTS generation successful for text length: {len(text_to_speak)}")

        return StreamingResponse(
            io.BytesIO(audio_bytes),
            media_type="audio/mpeg",
            headers={
                "Content-Disposition": "inline; filename=response.mp3",
                "Cache-Control": "no-cache",
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"TTS error: {e}")
        raise HTTPException(status_code=500, detail=f"Text-to-speech failed: {str(e)}")