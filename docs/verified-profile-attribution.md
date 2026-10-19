# Restore verified department and job title

The conversation-security patch stopped accepting browser-supplied profile
fields. Microsoft login tokens do not supply the department/title fields in
this deployment, although the frontend could previously obtain them through
Microsoft Graph. New sessions therefore recorded null values.

The updated client silently acquires a delegated User.Read token and sends it
as X-Graph-Token alongside the application's Microsoft ID token. The backend
uses the Graph token only with the fixed HTTPS Graph /me endpoint, requests
id, department and jobTitle, and accepts profile fields only when the returned
id matches the oid in the verified application login. Graph tokens are never
accepted as application authentication. Profile data never changes the user's
identity, permissions or conversation namespace.

This follows [Microsoft's Get user API](https://learn.microsoft.com/en-us/graph/api/user-get?view=graph-rest-1.0):
/me uses delegated User.Read permission, and $select requests the profile fields.
No new application-wide directory permissions, client secret or dependency is
required. The existing frontend already requests User.Read.

Matching profiles are cached in process for five minutes with a 512-entry cap.
Cache keys include the verified principal and a token digest; tokens themselves
are not cached, logged or stored in the database. Graph failures, missing consent,
missing credentials and profile mismatches leave chat available without adding
unverified fields. The request timeout is three seconds. Existing non-null session
metadata is preserved by the existing COALESCE update behavior.

Text chat and both voice provider paths forward the Graph credential. For voice,
it is acquired at call start; if it expires during a long call, enrichment is
best-effort and the existing session metadata remains. Start a new call to acquire
a fresh token. Tokens are sent in HTTPS headers or the authenticated WebSocket
setup frame, never URL query strings. Do not include headers or WebSocket setup
payloads in diagnostic logs.

After deploying frontend and backend together, refresh the browser and send a
message while signed in. The resulting session should show department and position
in the admin list and detail view when those attributes exist in the directory.
An existing chat_v2 session with missing metadata can be enriched on its next turn.
Closed historical sessions are not automatically backfilled. Existing conversation
IDs and histories remain unchanged by this patch.

Validation uses mocked Graph and AI services: matching and mismatched users,
unavailable Graph, guest/no-token behavior, cache isolation/expiry, actual chat
route recording, and voice token forwarding. Live tenant consent and real directory
values still need deployment verification. No production data was read or modified
while implementing this patch.
