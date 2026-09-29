# Admin access and deployment

Every `/api/v1/admin/*` endpoint requires a Microsoft token validated by the
backend (signature, expiration, issuer and application audience), followed by a
server-side administrator check. The frontend attaches the API token separately
from any Microsoft Graph token used for file ingestion.

## Configuration

- `MS_TENANT_ID` and `MS_CLIENT_ID`: the existing Microsoft tenant and application.
- `ADMIN_EMAILS`: comma-separated approved Microsoft account usernames, held in
  the backend environment. If absent, the backend uses its existing
  `VITE_ADMIN_EMAILS` environment value for compatibility. An explicitly empty
  value denies all administrators. Keep the frontend's `VITE_ADMIN_EMAILS` aligned
  so the same users can navigate to the admin pages. The frontend list alone does
  not grant API access.
- `ADMIN_AGENT_MAP`: existing JSON mapping of approved administrator emails to
  allowed knowledge-base agent IDs; `["*"]` grants all agents. This does not by
  itself grant administrator access. Missing or invalid permissions deny access.

Identity comes from the signed token's `preferred_username` (or `email` if that
claim is absent), with an `oid` required. Legacy `user_email` request fields are
ignored for authorization. The existing email policy is retained; future account
renames must be reflected in the allowlists.

The agent map governs ingestion and knowledge-base operations. Approved admins
retain the existing dashboard permission to view all agents' conversations and
feedback. SharePoint now follows the same agent permission checks as OneDrive;
URL ingestion checks the actual target collection.

`AZURE_AUTH_ENABLED=false` only affects the existing chat development path;
it cannot bypass admin authentication.

## Rollout

Deploy the backend and frontend together. From the production checkout, using
the existing deployment environment:

```sh
docker compose -f docker-compose.prod.yml up -d --build backend frontend
```

Confirm the production backend receives the approved-email configuration. No
database migration or additional runtime dependency is required. This code
change does not deploy itself or establish whether any earlier access occurred.

The existing `backend/scripts/monthly_kb_refresh.py` caller now also requires
`ADMIN_API_TOKEN`, a current Microsoft API token for an approved administrator.
An email alone no longer authenticates the script. Tokens expire; unattended
monthly runs need a separately configured token acquisition/renewal mechanism.
Do not save a short-lived token as a permanent scheduled-job credential. Without
one, the script stops at its initial backend check before clearing collections.

After deployment:

1. An unauthenticated request to `/api/v1/admin/ingestion-status` must return 401.
2. A valid Microsoft token for an unapproved account must return 403.
3. The supervisor should sign in and verify dashboard, chat browsing, feedback,
   knowledge-base listing, and ingestion for an allowed agent.
4. Confirm direct requests with forged `user_email` values cannot grant access.

The `/admin` login page remains publicly reachable; seeing that page does not
grant administrative access. Hiding the URL is not an authorization control.

## Regression checks

With backend dependencies installed:

```sh
python -m unittest discover -s backend/tests -v
```

The tests register the actual admin routers and use locally signed test tokens.
Ingestion, databases, and Microsoft signing-key discovery are mocked; no live data
is accessed or deleted. Tests exercise every registered admin route for anonymous
and non-admin rejection, invalid tokens, forged emails, agent permissions, and
the development authentication switch. Build the frontend with `npm run build`
from `frontend`.
