# Conversation and identity protection — 7 October 2026

Prepared locally on `Other-security-fixes`. Not yet committed, pushed or deployed.

## Changes

- Chat POST and history GET validate supplied Microsoft tokens even if `AZURE_AUTH_ENABLED=false`. Private agents still require login.
- Stored conversation IDs are derived from the agent, verified tenant/object ID, and client thread ID. Public guests instead supply an independent 256-bit session credential in `X-Chat-Session`. The credential is generated using browser cryptography and persisted in sessionStorage, with an in-memory fallback if iframe storage is blocked. It is a bearer credential: keep it private and do not log it.
- Knowing another person's client thread ID or stored checkpoint ID does not select their conversation. Another identity gets a separate namespace (empty history for a new thread), rather than an ownership-transfer operation. Agent IDs and Microsoft tenants are also isolated.
- The chat route replaces body-supplied user ID, name, department and job title with verified identity values before logging, session recording, guardrails or graph/tool state. Missing department/job-title claims become null. HR tools therefore receive the verified account's username/email rather than the identity typed into the request. The existing username-to-employee-ID mapping is retained; verify this matches the organization's account provisioning.
- The shared `default_thread` fallback is removed: callers must supply a nonempty thread ID. Existing browser clients already do.
- Session-record upserts no longer transfer ownership to a different user.
- Feedback reads, writes and deletion use the same conversation namespace and verified identity. This fixes the related feedback impersonation gap and keeps new ratings joined to the correct admin conversation record. Feedback can still be submitted for a caller's own arbitrary message index; existence/rating abuse limits are separate work.
- Standard and iframe clients send the appropriate credentials. Microsoft token acquisition failure does not silently become a guest session.

No Python or npm dependency additions, database migrations, or image rebuilds are required.

## User-visible transition

Users start fresh conversations after deployment. Old messages and ratings are not deleted and remain accessible through authorized admin endpoints. Raw legacy thread IDs are deliberately never used as a fallback: historical owner labels were caller-controlled, so assigning them automatically would preserve the vulnerability.

New conversations persist across refreshes with the same account or guest browser session and the same client thread ID. Losing the guest credential starts fresh; there is no unauthenticated recovery by thread ID. External callers to public chat/feedback endpoints must send a persistent per-user/session 64-character lowercase hex `X-Chat-Session` credential generated from 32 cryptographically random bytes. Private integrations require valid Microsoft tokens. Deploy frontend and backend together and refresh already-open browser tabs.

The separate LifeStore MCP endpoint is not changed by this patch. Its browser feedback requests receive the guest credential; the shared LangGraph chat API is the conversation storage path covered here. Static evidence-image permissions, rate limits, CRM diagnostics and the Vite production-server finding remain separate remediation work.

## Verification

- 27 backend tests passed: existing admin/helpdesk checks plus real chat/feedback routers with RSA-signed test JWTs and synthetic graph/database services.
- Tests cover private access denial with development auth disabled, forged employee fields, cross-user read/write separation, guest separation, legacy-thread isolation, malformed/missing identities and thread IDs, tenant/agent separation, and feedback delete/read scope.
- 2 Node tests passed for guest credential persistence, independent sessions and blocked iframe storage.
- Frontend production build passed. Existing Browserslist-age and large-bundle warnings remain; no dependency upgrade was attempted.
- Backend tests and build required approved execution outside the Windows sandbox because it blocked asyncio loopback sockets and Vite file resolution. No live model, HR API, CRM or production database calls were used.

Commands: `python -m unittest discover -s backend/tests -v`; from frontend, `node --test src/chatSession.test.js` and `npm run build`.

## Deployment sequence

Commit and push the reviewed changes first. On `/opt/Ask_SLT`, ensure the worktree is clean of tracked changes, fetch and fast-forward `Other-security-fixes`, confirm `/app` is bind-mounted from `/opt/Ask_SLT/backend` in `slt_backend` and `/opt/Ask_SLT/frontend` in `slt_frontend`, then restart both containers. Use the existing dependencies; do not repeat the large backend image build that exhausted server disk. A coordinated restart/refresh is needed because old anonymous browser requests lack the new credential.

Before restarting, retain the previous commit for rollback. Rollback restores the earlier vulnerabilities and must not be described as a secure long-term solution.

Verify startup and anonymous 401 responses on helpdesk, admin statistics, private chat history and private feedback. Public history without a guest credential should also return 401. Public history with a fresh valid guest credential and a nonexistent thread should return an empty message list; this read does not use AI tokens. Signed-in history and feedback should work for the same caller and remain isolated from another test identity. Reading a new namespace may initialize an agent's checkpointer/schema; this is not a strictly database-write-free probe.

After the model API recovers, verify full message generation and HR lookup using the account holder's own data. Until then, the mocked tests establish code behavior, while live GET checks can validate routing, authentication and storage connectivity without AI inference.
