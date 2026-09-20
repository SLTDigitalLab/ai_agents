# Workmate with the Napster avatar

`/workmateai/voice/agent` renders the configured Napster companion and voice.
Napster captures microphone speech and presents the returned Workmate answer.

## Request flow

1. `backend/routers/napster.py` reads the existing agent and validates its implicit
   `answer` function, then creates a connection at Napster's `/public/connections`
   with `functions: ['answer']`. It reuses the agent's companion and voice.
2. `useNapsterAvatar.js` gets a temporary token from `POST /api/napster/session`,
   initializes SDK 1.5.0 and forwards `onData` events to `WorkmateBridge.js`.
3. The bridge handles `function_implicitly_called` for `answer`. It reads
   `data.call_id` and `data.arguments.user_message` (object or JSON arguments).
4. It calls the existing `POST /api/v1/chat` with the existing schema:

   ```json
   {
     "message": "What services can you help me with?",
     "agent_id": "supervisor",
     "user_id": "<signed-in username>",
     "user_name": "<display name>",
     "thread_id": "<conversation ID>",
     "stream": false
   }
   ```

5. Workmate executes its existing supervisor, retrieval, specialist/API tools
   and any final synthesis. The current local configuration selects OpenAI
   `gpt-4o-mini`; the avatar bridge does not select or invoke a model.
6. The bridge validates the returned `response` string and sends:

   ```javascript
   companionInstance.sendCommand({
     type: 'send_function_output',
     data: {
       call_id: callId,
       output: {
         success: true,
         message: workmateResponse + ' [speak verbatim]',
       },
       delay: false,
     },
   });
   ```

Experimental waiting voice: if the Workmate request is still pending after four
seconds, the bridge sends a separate Napster `send_message` asking it to speak
"I'm still checking that for you. This may take a little longer." The original
implicit call remains pending, and its original `call_id` is used for the final
`send_function_output`. Diagnostics include `waiting_voice_sent`,
`original_call_id`, and `final_function_output_sent`. This has automated bridge
coverage but still requires a live Napster acceptance test to confirm that the
provider speaks the status without expiring the pending function call.

The answer string is preserved, including whitespace, Unicode and formatting.
The only addition is the requested control suffix. The bridge never sends an
answer through `send_message` and does not invoke another answer generator.
The existing text chat and separate voice-only routes retain their own flows.

## Configuration

### Backend settings

Keep `NAPSTER_API_KEY` and `NAPSTER_AGENT_ID` in the backend environment.
The agent must already have a companion, voice and implicit `answer` function
whose arguments include `user_message`. Credentials stay server-side; the
session endpoint returns only the temporary token with `Cache-Control: no-store`.
The frontend uses its existing `VITE_API_URL` configuration.

## Timeout investigation and remaining limitation

The installed `@touchcastllc/napster-companion-api` version is **1.5.0**.

- `lib/index.esm.js`: the public `sendCommand` forwards the command over the
  WebRTC data channel. The bundled Edge MCP tool dispatcher awaits
  `executeTool(...)` and then sends `send_function_output`. It contains no
  mechanism that renews the server's implicit-function deadline.
- `lib/types/index.d.ts`: SDK inactivity settings control idle-session behavior,
  not tool execution deadlines.
- `lib/services/edge-mcp/edge-mcp-bridge.d.ts`: `attachTimeoutMs` limits waiting
  for the browser's tool registry, not waiting for an external AI answer.
- The inspected public session settings expose no implicit-tool timeout override.

[Napster's tool execution documentation](https://developers.napster.com/docs/building-your-omniagent/tools/executing-tools)
documents a default 10-second tool deadline. Its supported deferred implicit
result pattern completes the tool with an interim result, then uses
`send_message` for the eventual result. That pattern conflicts with this
integration's required command flow and is not implemented.

`WORKMATE_REQUEST_TIMEOUT_MS = 30_000` is strictly the bridge's local HTTP
deadline (including reading the response body). It does **not** change Napster's
deadline. The existing backend `VOICE_CHAT_TIMEOUT_SECONDS` setting belongs to
the separate realtime voice bridge; this avatar calls `/api/v1/chat` directly.

If Napster emits `function_call_timeout`, the bridge aborts its pending request,
stops the avatar session and prevents late answer delivery. Increasing the HTTP
deadline cannot make a 24.5-second response work under a 10-second provider limit.
There is no verified supported long-running implicit-call solution in this SDK
or the inspected documentation. A provider-supported extension, or Workmate
responses within the provider's deadline, is required for that case.

## States, errors and validation

The UI reports listening, thinking, preparing and speaking states. Duplicate
call IDs are ignored. The microphone stays open so the customer can interrupt.
On `speech_started` / `interrupted`, the bridge mutes the old audio and uses the
SDK's `stopAvatarTalking()` to cancel playback without destroying the connection.
It clears the old speech timers, aborts any pending local request, and returns to
Listening. The next implicit `answer` call uses the same Workmate conversation.
A fresh call that arrives before the speech event also supersedes the previous
turn. Aborting a local HTTP request prevents delivery of its obsolete response;
it is not a guarantee that server-side generation stops immediately.

Retired call, response and item IDs prevent late timeout, cancellation and
transcript events from invalidating the new answer. Untagged transcripts after
an interruption are skipped and logged as uncorrelated, not marked verified.
Unsolicited startup/late speech is muted and cancelled without disconnecting.
HTTP/network failures, invalid/empty answers, expiry, disconnects and playback
failures receive user-facing errors.

Connection instructions request verbatim speech. When a completed assistant
transcript is supplied, the bridge compares it with the original answer (allowing
whitespace, Unicode composition, paired Markdown bold delimiters, and an optional
colon after a leading list heading in the comparison, while preserving words,
numbers, punctuation within content and source links) and
stops the session on a mismatch. This is a detection check, not a speech-only API
guarantee: a completed transcript may arrive after speech has already played.
An incomplete prefix is checked after normal speech completion, allowing a user
interruption to arrive before a cutoff sentence is treated as missing content.
Exact speech and lip-sync still require a live browser acceptance test.

Run the automated checks from the repository root:

```powershell
backend/venv/Scripts/python.exe -m unittest discover -s backend/tests -p test_napster.py -v
backend/venv/Scripts/python.exe -m unittest discover -s backend/tests -p test_voice_avatar.py -v
cd frontend
npm run test:avatar
npm run build
```

Tests cover the request schema, unchanged output, duplicate events, failures,
cancellation, provider expiry and simulated 24.5-second responses. A slow-response
test with no provider expiry demonstrates only the local HTTP behavior; another
test verifies that a provider expiry at 10 seconds prevents that late response.

For live acceptance, start the avatar and ask "What services can you help me
with?" Check the displayed Workmate answer, outgoing function output, audio and
lip-sync. Diagnostics report call IDs, stages, character counts and transcript
match results without logging answer text or API keys. Do not mark acceptance
complete if the provider expires the call or the spoken answer differs.
