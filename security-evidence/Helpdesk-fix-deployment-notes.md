# Helpdesk HTTP permissions — first remediation

Prepared 6 October 2026. Local changes only; not deployed.

## Resulting policy

All `/api/v1/helpdesk_dev/*` endpoints require a verified Microsoft bearer token, including when development chat authentication is disabled.

- Ticket lists default to the verified user's Microsoft username/email. They no longer return an unfiltered list when `userId` is omitted. Approved administrators can explicitly select another user.
- Individual ticket reads require ownership or existing administrator authorization. Another user's ticket is reported as not found to ordinary users.
- Ticket creation defaults to the verified user; only approved administrators can select another user or set ticket IDs and workflow-status fields.
- Solved-ticket and category HTTP reads/writes require the existing administrator allowlist. The local agent continues to use database service functions directly.
- Missing verified identity fails closed. No new allowlist or password is introduced. Existing `ADMIN_EMAILS` / `VITE_ADMIN_EMAILS` administrator policy applies.

## Integration checks before deployment

The repository's local helpdesk tools call the database service directly. The `helpdesk_dev` chat agent also forwards to an external n8n workflow whose nodes are outside this repository. Confirm whether that workflow calls these HTTP endpoints before deploying. Existing unauthenticated external HTTP callers will receive 401; do not deploy this assuming they will continue working. If needed, implement narrowly scoped service authentication and update the workflow as a coordinated follow-up. Do not put an administrator's long-lived token in a browser or workflow configuration as a workaround.

Existing ticket ownership uses Microsoft usernames/emails, not immutable object IDs. The HTTP policy normalizes them to lowercase. Check historical ticket owner formats before rollout; differently formatted records should be migrated through an authorized process rather than broadening access. The database layer itself is unchanged.

This patch addresses the direct helpdesk HTTP exposure. The separate chat identity and conversation-ownership findings remain outstanding; this patch does not make those paths safe or prove that all agent/tool actions are authorized.

## Validation

Result on 6 October 2026: all 19 tests passed (11 existing admin tests and 8 new helpdesk tests). The initial sandboxed run stalled creating Windows' asyncio loopback socket; the approved run outside the sandbox completed in 5.212 seconds. `git diff --check` also passed.

`backend/tests/test_helpdesk_security.py` exercises the actual router and Microsoft JWT verification with synthetic RSA-signed tokens and mocked database services. It checks all routes for anonymous/invalid/expired/wrong-audience/wrong-issuer rejection, including disabled chat authentication; owner isolation; spoofed identity and workflow fields; missing identity; allowed administrator operations; and an empty administrator allowlist.

Run with the existing admin suite: `python -m unittest discover -s backend/tests -v` using an environment with the required FastAPI, httpx, python-jose, cryptography, requests and BeautifulSoup dependencies.

After deployment, use an anonymous GET with a nonexistent user filter to verify HTTP 401. Use test accounts and synthetic tickets to verify owner access, other-user denial and approved administrator access. Confirm local helpdesk and any external n8n integration behavior. No production writes or deployment were performed while preparing this patch.
