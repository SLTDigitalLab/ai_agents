# WorkMate staging deployment

The staging runtime is deliberately separated into three parts:

- Git contains application code and deployment definitions.
- GHCR contains immutable application images tagged with the Git commit SHA.
- `/root/slt-app` contains secrets and persistent data. It must never be committed.

The Compose project is named `workmate-staging`. It does not use global
`container_name` values, publish database ports, or mount application source code.
This prevents WorkMate from colliding with unrelated projects on the VM.

## Required server files

Before deployment, verify these exist:

```sh
test -f /root/slt-app/.env
test -f /root/slt-app/service-account.json
test -f /root/slt-app/data/real/lifestore_all.json
chmod 600 /root/slt-app/service-account.json
```

The `.env` file should include the production provider configuration. In
particular, Gemini-first voice operation currently requires `VOICE_PROVIDER=gemini`,
the correct Vertex project/location, and valid Google credentials. Do not keep a
second service-account JSON file inside `backend/routers`.

## Build pipeline

Every push to `voice-test` builds and publishes three images to GitHub Container
Registry. Each release has an immutable full-commit tag and a movable
`voice-test` convenience tag. Deploy the immutable tag.

The backend and MCP targets share the same Python dependency layer. This avoids
maintaining two separate 12 GB dependency installations and ensures the MCP
packages declared in `backend/requirements.txt` are present in both images.

## First clean deployment

Use the Docker daemon selected by the server owner. Do not use `nsenter` in a
permanent deployment. Resolve the duplicate Docker-daemon installation first and
confirm that `docker info` shows the daemon that owns the intended containers.

Export the three image names using the exact successful workflow SHA:

```sh
export WORKMATE_BACKEND_IMAGE=ghcr.io/OWNER/REPOSITORY/backend:FULL_COMMIT_SHA
export WORKMATE_MCP_IMAGE=ghcr.io/OWNER/REPOSITORY/mcp-lifestore:FULL_COMMIT_SHA
export WORKMATE_FRONTEND_IMAGE=ghcr.io/OWNER/REPOSITORY/frontend:FULL_COMMIT_SHA
```

Then validate, pull, and start only the WorkMate project:

```sh
docker compose -f docker-compose.prod.yml config --quiet
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d --remove-orphans
docker compose -f docker-compose.prod.yml ps
```

Check the application without exposing internal database services:

```sh
curl -fsS http://127.0.0.1:8100/api/v1/realtime/provider
curl -fsSI http://127.0.0.1:3100/
docker compose -f docker-compose.prod.yml logs --tail=100 backend mcp-lifestore frontend
```

Only after QA passes should old WorkMate containers and dangling images be
removed. Never run broad volume pruning on this shared VM.

## Rollback

Set the three image variables back to the previous commit SHA and run:

```sh
docker compose -f docker-compose.prod.yml up -d
```

Persistent PostgreSQL, Qdrant, evidence, credentials, and LifeStore catalog data
remain under `/root/slt-app`, so changing an application image does not replace
them.
