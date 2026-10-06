# Production security review — 2 October 2026

Production: https://aiagents.sltdigitallab.lk

Reviewed repository: branch `admin-security-fix`, commit `9ed64d502916d1e825ebac7ace50b8ba371ae13e`. The exact running image/commit was not independently verified.

## Result and scope

Additional significant access-control gaps remain outside the admin routes. The two admin endpoints checked correctly reject anonymous requests with HTTP 401. This supports the earlier fix for those endpoints; it is not proof that the entire application is secure.

The assessment combined source/configuration review, eight narrowly scoped production GET requests, five local tests with synthetic data and mocked services, and a frontend dependency audit. Production checks sent no credentials and used a random nonexistent identifier for data endpoints. No actual private conversation or ticket was retrieved, no production write endpoint was exercised, and no LLM, email, CRM or HR lookup was triggered. This is a targeted application review, not a complete infrastructure penetration test or incident investigation.

## Prioritized findings

### 1. High — Helpdesk APIs lack authentication and ownership checks

Evidence: `backend/routers/helpdesk.py:22–80`, `backend/services/helpdesk_tickets.py:211–261`.

The ticket list and individual ticket handlers accept callers without a token. The user filter is optional and supplied by the caller; omitting it permits an unscoped query. Creation also accepts a caller-selected user identity. Solved-ticket and category creation routes similarly lack permission checks. The `_dev` name does not prevent these endpoints from being registered in production.

Live confirmation: an anonymous GET to the ticket list with a random nonexistent user filter returned 200 and an empty ticket list. Local synthetic tests reproduced anonymous retrieval and ticket creation using another user's identity. Real production records and write operations were deliberately not tested.

Impact: potential disclosure of support tickets and user details, fabricated tickets, and modification of the solved-ticket knowledge source.

Fix: require a verified user or explicitly authorized service identity; derive ordinary-user filters from that identity; enforce ticket ownership; restrict cross-user reads and solved-ticket/category writes to appropriate roles. Preserve internal agent integrations through explicit service authorization rather than a publicly unauthenticated route.

### 2. High — Conversation access is not bound to the owner

Evidence: `backend/routers/chat.py:51`, `:459–552`, `:712–739`; `backend/schemas/chat.py:58`, `:137`; `backend/services/sessions.py:98`.

The chat guard checks whether an agent requires login. It does not check whether the caller owns the supplied conversation identifier. History loads graph state by the supplied thread ID. Private-agent callers therefore need a valid login but can address another user's known thread; public-agent history does not require login. The message route also accepts a supplied thread ID. UUIDs reduce guessing but do not establish ownership. Missing thread IDs fall back to the shared predictable value `default_thread`.

Local synthetic tests reproduced a signed-in second user reading an HR thread belonging to a first user, and an anonymous caller reading a public-agent thread. The control test correctly rejected anonymous HR access. No real production history was accessed.

Fix: create server-managed conversation ownership/membership and enforce it before both history reads and message processing. For guests, use a server-issued session credential bound to the conversation. Generate a unique thread for a new conversation, remove the shared default, and prevent session upserts from transferring ownership.

### 3. High — Client-supplied identity reaches HR tools

Evidence: `backend/routers/chat.py:502`, `:550`; `backend/domain/tools/api_tools.py:30–44`, `:90–104`; `backend/domain/archetypes/kb_api_agent.py:76`, `:118`.

Although private chat requires login, graph state takes `user_id` from the request body rather than the verified token. The leave-balance tool extracts an employee identifier from this state and uses it for the upstream request. The prompt's privacy instruction describes that value as authenticated, but the server has not bound it to the authenticated caller.

Impact: a signed-in caller could select another employee identity for a lookup if the upstream service accepts that identifier. Audit attribution can also be forged. This is a traced source-level finding; neither the upstream HR service nor production employee records were queried.

Fix: derive the internal identity from verified token claims and an authoritative employee mapping; disregard caller-selected identity for protected operations. Enforce authorization at the tool/service boundary as well as the chat route. Test that a valid user A cannot submit user B's identity.

### 4. High priority — Public production Vite development server; affected dependency in lockfile

Live confirmation: `/` includes the Vite dev client and `/@vite/client` returns its JavaScript with HTTP 200. `frontend/Dockerfile:13` runs `npm run dev -- --host`; the repository lockfile specifies Vite 7.3.1.

The maintainer's [GHSA-p9ff-h696-f583 advisory](https://github.com/vitejs/vite/security/advisories/GHSA-p9ff-h696-f583) includes Vite 7.3.1 in its affected range. It describes arbitrary file reading when an exposed development server's WebSocket is reachable under the stated conditions. The exact live Vite version and exploitable WebSocket path were not verified; no file-read exploit was attempted. Linux production should not be labeled vulnerable to separate Windows-only advisories.

Fix: build the frontend and serve the resulting static assets with a production web server, following [Vite's deployment guidance](https://vite.dev/guide/static-deploy.html). Remove development-server exposure and source mounts; update dependencies and rebuild reproducibly. Do not merely replace `vite dev` with `vite preview` as a production server. Keep backend secrets out of the frontend runtime environment.

The saved npm audit reports 19 affected package entries: 12 high, 4 moderate and 3 low. These include development/transitive dependencies and condition-dependent advisories; they are not 19 demonstrated production exploits. React Router SSR/RSC advisories must not be described as proven remote code execution in this client-side application. Backend dependencies and running container packages were not exhaustively audited.

### 5. Medium — Feedback can be read, forged and deleted without caller authorization

Evidence: `backend/routers/feedback.py:112`, `:169`, `:202`. The admin feedback view is guarded separately, but the underlying feedback endpoints lack caller/ownership checks.

Live confirmation: anonymous feedback lookup for a random nonexistent HR thread returned 200. A local test reproduced deletion using a supplied other-user identity against a mocked database. Production feedback was not read or changed.

Fix: bind feedback to the verified user or guest conversation credential; verify access to the conversation; allow only the author or an authorized administrator to change/delete it. Return only the feedback fields needed by that caller.

### 6. Medium — Production registers an unauthenticated CRM test-write endpoint

Evidence: `backend/routers/enterprise.py:112`; production OpenAPI lists `/api/v1/enterprise/test-webhook`.

The handler forwards caller-selected fields to the configured Bitrix24 webhook without authentication. If the integration is configured, it allows junk lead creation through the application's credentials. No production POST was sent, so integration success is not verified.

Fix: remove this diagnostic endpoint from production or require an appropriate administrator role. Apply validation and abuse controls to the intentionally public lead-submission journey as well.

## Additional issues requiring configuration/data verification

- **Evidence images:** `backend/main.py:87–95` mounts generated evidence images as unauthenticated static files. `backend/services/ingestion.py:759–780` generates image references. If these contain protected document material, anyone with the URL can bypass document permissions. No private image was fetched. Serve protected images through an authorized handler or short-lived scoped links.
- **Database credentials:** production compose contains a hardcoded weak/default database password and connection string. Its value is deliberately omitted here. Confirm the actual database credential and overrides on the server; move credentials out of tracked compose and rotate the database credential if the default is active. A PostgreSQL volume may retain an older password despite environment changes. This does not establish public database-port exposure.
- **URL-ingestion egress:** admin URL ingestion permits arbitrary URLs and follows redirects without an evident private-address restriction. This requires admin access after the earlier fix, but can still cross internal network boundaries. Verify intended destinations; reject disallowed schemes and private/metadata addresses on initial resolution and each redirect. No SSRF request was attempted.
- **Abuse controls:** no application/repository rate limiter was identified for public chat and externally consequential submission endpoints. Edge controls may exist outside the repository. Verify per-session/IP quotas, concurrency and cost limits, and external submission controls. No load testing was performed.
- **Response headers:** checked production responses lacked HSTS, CSP, X-Content-Type-Options and X-Frame-Options. Add suitable policy at the edge. Account for authorized website iframe embedding; do not blindly block all framing. Absence alone is not proof of a successful attack.
- **Error details and API documentation:** some handlers expose exception details or tracebacks. Return generic client errors and keep detailed logs server-side. Public OpenAPI is not itself an authorization flaw, but its globally added security markers in `backend/main.py:65–70` are documentation only and incorrectly imply all endpoints enforce authentication.

## Verification and rollout criteria

Prioritize helpdesk authorization, identity/ownership enforcement, and removal of the production development server. Then address feedback and CRM diagnostics, followed by the configuration issues above.

For each change, verify anonymous denial on protected endpoints, user-A/user-B isolation, expected supervisor access, authorized service integrations, and continued operation of intended public agents. Validate permission enforcement on the backend, regardless of browser changes. For the frontend, confirm dev-client routes are absent after deployment and built assets load correctly. Repeat the dependency audit after upgrades and check the actual running image versions.

These authorization practices align with OWASP's [authorization guidance](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html) and [object-access guidance](https://cheatsheetseries.owasp.org/cheatsheets/Insecure_Direct_Object_Reference_Prevention_Cheat_Sheet.html).

No fixes were deployed during this review. Production firewall rules, SSH security, cloud IAM, effective secret values, database permissions, backups, OS/container patch levels and incident logs still require direct server-side review. Findings do not establish how the earlier visitor accessed the dashboard or whether additional historical access occurred.

## Evidence files

- `production-readonly-results.json`: eight production checks at 10:37:22–24 UTC (16:07:22–24 Sri Lanka time), 2 October 2026.
- `production_readonly_audit.py`: bounded GET-only checker; no credentials or full record bodies saved.
- `test_additional_access_gaps.py`: five local synthetic tests. Selected source functions are compiled unchanged into lightweight routers; database/external dependencies are mocked. Passing means the access flaw was reproduced, not fixed. This is not a full application integration test.
- `frontend-dependency-audit.json`: npm audit output; dependencies were not automatically modified.
