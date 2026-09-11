# Workmate voice with an optional visual avatar

- `/voice` and `/workmateai/voice` render the original Workmate `VoiceAgentPage` from `voice-test`.
- `/workmateai/voice/agent` renders that same page alongside `SimliAvatarPanel`.
- All existing chat, admin, LifeStore and other application routes remain registered.

The Workmate voice page and its eight supporting components, plus the backend
`routers/voice_agent/realtime.py`, were copied unchanged from `voice-test`.
The voice provider, microphone, playback, knowledge-base tool calls, transcripts,
call controls, and theme controls remain owned by those original components.

The Simli panel reuses the connection lifecycle from
`Visual_AI_Agent_3.0/frontend/src/App.jsx` and renders the existing `Avatar.jsx`.
It is a **silent visual preview**, matching that source project. It does not
send microphone or assistant audio to Simli and does not implement lip-sync.
Avatar stop/reconnect controls affect only the avatar. Leaving the visual route
cleans up both components through their own unmount handlers.

## Configuration

The existing FastAPI app in `backend/main.py` exposes `POST /api/simli/session`.
It returns only a temporary session token and keeps credentials server-side.
Configure `SIMLI_API_KEY` and `SIMLI_FACE_ID` in `backend/.env` for development
(or the root `.env` used by production Compose). No `VITE_SIMLI_*` values are needed.

The session follows the source project's 600-second duration/idle limits and
LiveKit transport. The avatar connects when the visual page opens. Workmate's
voice call still starts with the existing Start control.

Voice uses the original `/api/v1/realtime/*` routes. Its existing provider settings
include `OPENAI_API_KEY`, or `GOOGLE_APPLICATION_CREDENTIALS` and `PROJECT_ID` for
Gemini. `VOICE_PROVIDER=openai` is the original explicit provider override.
`VOICE_CHAT_TIMEOUT_SECONDS` defaults to 30.

The frontend uses the existing `VITE_API_URL` setting for backend requests.
Restart the backend after environment changes and run `npm install` in `frontend/`.

## Chat compatibility

The voice source expects `POST /api/v1/chat` with `stream: false` to return
`{"response": "..."}`. This option consumes the existing guarded, checkpointed
chat generator. The default remains streaming, including citations/evidence.
Hidden evidence metadata is omitted from the voice JSON answer.

## Validation

```powershell
backend/venv/Scripts/python.exe -m unittest discover -s backend/tests -p test_voice_avatar.py -v
cd frontend
npm run build
node node_modules/eslint/bin/eslint.js src/components/SimliAvatarPanel.jsx src/pages/VoiceAvatarPage.jsx
```

Backend tests isolate the actual endpoint functions to avoid unrelated model and
database startup. They mock provider calls and verify token redaction, missing
configuration, invalid responses, timeout handling, default chat streaming,
voice JSON output, and preserved guardrail behavior.

A live check needs configured databases/model credentials, Microsoft login where
applicable, microphone permission, and Simli connectivity. Check both routes:
start/end a voice call, confirm a knowledge-base answer, stop/reconnect the avatar,
and navigate back to the voice-only page.
