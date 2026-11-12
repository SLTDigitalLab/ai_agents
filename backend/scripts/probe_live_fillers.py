"""Manual Gemini Live filler probe. Sends no employee data; prints metadata only."""
import asyncio
import argparse
import base64
import json
import time

import websockets

from core.config import settings
from routers.voice_agent.realtime import _get_vertex_access_token, GEMINI_LIVE_MODEL, resolve_voice_language


async def main(language):
    phrase = {
        'en': 'Let me check that for you.',
        'si': '\u0dc4\u0dbb\u0dd2, \u0db8\u0db8 \u0d91\u0d9a \u0db6\u0dbd\u0db1\u0dca\u0db1\u0db8\u0dca.',
        'ta': '\u0b9a\u0bb0\u0bbf, \u0ba8\u0bbe\u0ba9\u0bcd \u0b85\u0ba4\u0bc8 \u0baa\u0bbe\u0bb0\u0bcd\u0ba4\u0bcd\u0ba4\u0bc1 \u0b9a\u0bca\u0bb2\u0bcd\u0b95\u0bbf\u0bb1\u0bc7\u0ba9\u0bcd.',
    }[language]
    language_name = {'en': 'English', 'si': 'Sinhala', 'ta': 'Tamil'}[language]
    token = _get_vertex_access_token()
    region = settings.LOCATION
    url = (f"wss://{region}-aiplatform.googleapis.com"
           "/ws/google.cloud.aiplatform.v1.LlmBidiService/BidiGenerateContent"
           f"?access_token={token}")
    model = (f"projects/{settings.PROJECT_ID}/locations/{region}"
             f"/publishers/google/models/{GEMINI_LIVE_MODEL}")
    async with websockets.connect(url, additional_headers={"Content-Type": "application/json"},
                                  max_size=10 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"setup": {
            "model": model,
            "generation_config": {
                "response_modalities": ["AUDIO"],
                "speech_config": {"voice_config": {"prebuilt_voice_config": {"voice_name": "Alnilam"}}},
            },
            "output_audio_transcription": {},
            "system_instruction": {"parts": [{"text": (
                "For the test question, call fake_lookup and wait for its result. "
                "If an internal [LUMA_FILLER_ACK] message arrives while the tool is pending, "
                "speak exactly its phrase with your current voice, without calling another tool."
            )}]},
            "tools": [{"function_declarations": [{
                "name": "fake_lookup", "description": "Read-only test lookup",
                "behavior": "NON_BLOCKING", "parameters": {
                    "type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"],
                },
            }]}],
        }}))
        await asyncio.wait_for(ws.recv(), timeout=12)
        async def stream_mic_silence():
            chunk = base64.b64encode(bytes(8192)).decode('ascii')
            while True:
                await ws.send(json.dumps({'realtime_input': {'media_chunks': [{
                    'mime_type': 'audio/pcm;rate=16000', 'data': chunk,
                }]}}))
                await asyncio.sleep(.256)
        mic_task = asyncio.create_task(stream_mic_silence())
        await ws.send(json.dumps({"client_content": {
            "turns": [{"role": "user", "parts": [{"text": "Use fake_lookup for a test value."}]}],
            "turn_complete": True,
        }}))
        call = None
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and call is None:
            data = json.loads(await asyncio.wait_for(ws.recv(), timeout=deadline - time.monotonic()))
            calls = data.get("toolCall", {}).get("functionCalls", [])
            if calls:
                call = calls[0]
        if call is None:
            print("tool_call=missing")
            return
        print("tool_call=received")
        await ws.send(json.dumps({"client_content": {
            "turns": [{"role": "user", "parts": [{"text": (
                f"[LUMA_FILLER_ACK] Speak only this short {language_name} processing phrase in {language_name}, "
                "using your current voice. Do not call tools or answer the original question. "
                f"Exact phrase: {phrase}"
            )}]}],
            "turn_complete": True,
        }}))
        audio_chunks = 0
        transcription_events = 0
        transcription = ''
        interruptions = 0
        cancellations = 0
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            try:
                data = json.loads(await asyncio.wait_for(ws.recv(), timeout=deadline - time.monotonic()))
            except asyncio.TimeoutError:
                break
            content = data.get("serverContent", {})
            audio_chunks += sum("inlineData" in part for part in content.get("modelTurn", {}).get("parts", []))
            transcription_events += bool(content.get("outputTranscription", {}).get("text"))
            transcription += content.get("outputTranscription", {}).get("text", "")
            interruptions += bool(content.get("interrupted"))
            cancellations += len(data.get("toolCallCancellation", {}).get("ids", []))
            if content.get("turnComplete") and audio_chunks:
                break
        print(f"filler_audio_chunks={audio_chunks} transcription_events={transcription_events} "
              f"interruptions={interruptions} tool_cancellations={cancellations} "
              f"output_language={resolve_voice_language(transcription) if transcription else 'unknown'}")
        mic_task.cancel()
        await asyncio.gather(mic_task, return_exceptions=True)
        if not cancellations:
            await ws.send(json.dumps({"tool_response": {"function_responses": [{
                "id": call["id"], "name": call["name"],
                "response": {"output": "Test complete"},
            }]}}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--language', choices=('en', 'si', 'ta'), default='en')
    asyncio.run(main(parser.parse_args().language))
