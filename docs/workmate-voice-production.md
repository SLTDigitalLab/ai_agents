# Workmate voice production integration

Prepared on October 7, 2026 on `codex/workmate-voice-production`.

## Branch history and scope

- Production base: `origin/Other-security-fixes`, commit `583c20e`.
- Earlier integration: `codex/workmate-voice-integration`, commit `d800466`,
  created September 13, 2026 from `41fa37e`. Its four voice commits had not
  reached the current production branch.
- Staging: `origin/voice-test`, commit `0a6c0f0`. Its Workmate voice implementation
  matches the original integration except that the earlier integration adds
  authentication forwarding, duplicate tool-call coalescing, and response grounding.
- This branch merges the focused integration into current production. It does
  not merge staging's unrelated Visual RAG, mobile, ingestion, and other changes.
- Current production helpdesk, admin authorization, conversation ownership,
  and verified employee identity remain in place.

The integration also requires verified Microsoft login before creating provider
sessions, derives Gemini identity from signed claims, and forwards the user's
token to the existing protected chat pipeline. Each call uses one conversation
ID; separate calls use separate IDs. Non-streaming chat returns complete answer
text without hidden visual evidence metadata. Existing text chat still streams.

## Configuration to verify on the production server

Use the existing production Microsoft settings (`MS_TENANT_ID`, `MS_CLIENT_ID`)
and frontend sign-in configuration. Voice never uses `AZURE_AUTH_ENABLED=false`
as an authentication bypass.

Choose the voice provider using the server's `.env`:

```dotenv
# Prefer Gemini with a valid Vertex service account; OpenAI is the fallback.
GOOGLE_APPLICATION_CREDENTIALS=/app/service-account.json
PROJECT_ID=your-vertex-project
LOCATION=us-central1
VOICE_CHAT_TIMEOUT_SECONDS=30

# For an explicit OpenAI selection / provider rollback:
# VOICE_PROVIDER=openai
# OPENAI_API_KEY=your-existing-server-key
```

The service account must exist inside the backend container and have access to
the selected Vertex project/model. Relative credential paths resolve from the
backend directory. Use secret values from the server's existing configuration;
do not commit credentials. Increase the chat timeout if measured pipeline
latency requires it.

Verify `VITE_API_URL` points at the production HTTPS API origin. The voice client
derives `wss://` from that URL. HTTPS is required for browser microphone access.
The repository's production Nginx configs already forward WebSocket Upgrade
headers for `/api/`; confirm the active server configuration matches them.

## Review and release

1. Review this branch against `Other-security-fixes`. If production moves,
   merge those new commits into the integration branch and repeat validation.
2. Test this exact branch on a release validation environment with production-like
   Microsoft authentication, provider credentials, HTTPS and reverse proxy routing.
3. Sign in and check Start Conversation, microphone permission, greeting, a
   workplace question, a follow-up question, interruption, End Call, and reconnect.
   Confirm microphone capture stops after End Call and failed connections.
4. Check unauthenticated `/api/v1/realtime/provider` and `/token` return 401.
   An unauthenticated WebSocket must close before contacting Vertex.
5. Smoke-test existing text chat, helpdesk, admin access, and conversation isolation.
6. Merge the reviewed branch into the production release branch and deploy
   through the normal production procedure. Rebuild the backend image because
   `google-auth` and `websockets` are newly declared dependencies. The repository's
   Compose frontend runs Vite; rebuild/restart it as required by your deployment.
   Avoid restarting or removing database volumes.

For the repository Compose deployment, the service rebuild command is:

```sh
docker compose -f docker-compose.prod.yml up -d --build backend frontend
```

Do not run it until the server checkout and configuration have been verified.
No production server restart or live deployment was performed while preparing
this branch.

## Rollback

Before release, record the currently deployed commit and image tags. Redeploy
those backend/frontend versions to roll back the feature. `583c20e` is the
production branch base observed during preparation, not a verified live server
snapshot. Keep production `.env`, provider credentials and database volumes.
No database migration is introduced by the voice integration.

## Local validation

```powershell
venv\Scripts\python.exe -m unittest discover -s backend/tests -v
cd frontend
npm.cmd run build
node --test src/chatSession.test.js
node_modules\.bin\eslint.cmd src/pages/VoiceAgentPage.jsx src/components/voice_agent
```

These checks use mocked providers and synthetic signed Microsoft tokens. Actual
microphone/audio quality, provider access and the live proxy still require the
release smoke test above.
