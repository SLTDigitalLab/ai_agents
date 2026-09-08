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
from typing import Literal
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI
from core.config import settings         

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/voice", tags=["voice"])

# Reuse the same OpenAI client pattern used elsewhere in the project
client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


# STT  
@router.post("/stt")
async def speech_to_text(
    audio: UploadFile = File(...),
    language: Literal["en", "si", "ta"] = Form("en"),
):
    try:
        audio_bytes = await audio.read()
        if len(audio_bytes) == 0:
            raise HTTPException(status_code=400, detail="Empty audio file received")

        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = audio.filename or "recording.webm"

        if language == "en":
            # Standard English transcription
            transcript = await client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                language="en",
                response_format="verbose_json",
            )

        elif language == "ta":
            # Tamil — use translation endpoint (speech → English text)
            # Whisper supports Tamil transcription but translation is more reliable
            transcript = await client.audio.translations.create(
                model="whisper-1",
                file=audio_file,
            )

        elif language == "si":
            # Sinhala — NOT supported by Whisper API
            # Use translation endpoint without language hint — Whisper auto-detects
            # and translates to English. Works reasonably for Sinhala speech.
            transcript = await client.audio.translations.create(
                model="whisper-1",
                file=audio_file,
            )

        logger.info(f"STT language={language}, text='{transcript.text[:80]}'")
        return {"text": transcript.text}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"STT error: {e}")
        raise HTTPException(status_code=500, detail=f"Speech-to-text failed: {str(e)}")


#  TTS  
@router.post("/tts")
async def text_to_speech(
    text: str = Form(...),
    voice: str = Form(default="alloy"),  
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