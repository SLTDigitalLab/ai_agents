# Operations

## Production deployment

From the project root, with the deployment `.env` configured:

```bash
docker compose -f docker-compose.prod.yml up --build -d
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs --tail 100 backend
```

The production Compose file binds the frontend to `127.0.0.1:3100` and the backend to `127.0.0.1:8100`. PostgreSQL and Qdrant are internal-only and use named volumes. Use the same `-f docker-compose.prod.yml` option for subsequent production commands. `down` preserves data; `down -v` deletes these named database volumes.

[The Nginx configuration](../nginx/aiagents.sltdigitallab.lk) proxies `/` to port 3100 and `/api/` to port 8100, with buffering disabled for streaming. Configure the hostname and TLS certificates on the host separately. Set `VITE_API_URL` to the public HTTPS origin and register its `/auth/callback` sign-in redirect. The supplied frontend container runs Vite behind Nginx; it does not serve a prebuilt static bundle.

The existing deployment documentation identifies `/opt/Ask_SLT` as the host directory and `https://aiagents.sltdigitallab.lk` as the public origin. Verify these against the target server. Agent pages include `/asklifestore`; iframe routes use `/:agentKey/iframe`.

## Optional services and integrations

- **Neo4j:** Neither Compose file starts Neo4j. Provision it separately and set `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, and optionally `NEO4J_DATABASE`. Use an address reachable from the backend container. The graph rebuild script is [build_lifestore_graph.py](../backend/scripts/build_lifestore_graph.py), at `/app/scripts/build_lifestore_graph.py` inside Docker.
- **LifeStore MCP:** The backend loads [server.py](../mcp_lifestore/server.py) in-process; a separate MCP container is not required for the UI. Compose sets the module path and `LIFESTORE_PRODUCTS_JSON=/app/data/real/lifestore_all.json`, backed by `backend/data/real/lifestore_all.json` on the host. Obtain the catalog data separately; `data/` is Git-ignored. The chat route uses `LIFESTORE_OPENAI_API_KEY` or `OPENAI_API_KEY`, with optional `LIFESTORE_OPENAI_MODEL`. Check `/api/v1/lifestore/mcp-health` to diagnose setup.
- **Ollama HR demo:** Configure `SLM_BASE_URL`, `SLM_EMBEDDING_BASE_URL`, `SLM_MODEL`, `SLM_EMBEDDING_MODEL`, and matching dimensions. Pull the configured models into your Ollama instance. Container `localhost` points to the container itself; on Docker Desktop, host services can use `host.docker.internal`.
- **Remote KB:** Set `KB_REMOTE_URL` and `KB_REMOTE_API_KEY` to use an authorized remote instance instead of local ingestion. The serving instance controls `DEV_KB_API_KEY` and `KB_RETRIEVAL_ALLOWLIST`; Finance’s separate retrieval endpoint uses `VOICE_ASSISTANT_API_KEY`.
- **Tracing:** Langfuse and optional LangSmith integration are available; configure credentials for the tracing service used by your deployment.

## Collections and monthly refresh

Regular agent collections use `<agent_id>_docs` for OpenAI embeddings and `<agent_id>_docs_gemini` for Gemini/Vertex embeddings. The Ollama demo uses `askhrslm_docs`. Changing embedding models or dimensions requires compatible vectors, usually through re-ingestion. `INGESTION_EMBEDDING_*` overrides support building a new provider’s collections before switching retrieval; see [config.py](../backend/core/config.py).

The [monthly refresh script](../backend/scripts/monthly_kb_refresh.py) ingests LifeStore and Enterprise websites and rebuilds the LifeStore Neo4j graph. Review its settings before running:

- `ADMIN_BASE_URL` is normally `http://127.0.0.1:8000` inside the backend container; configure `ADMIN_USER_EMAIL` with ingestion access.
- Refresh ingestion base names default to `lifestore` and `enterprise`; deletion targets add `_docs` and the embedding provider suffix. Check `LIFESTORE_QDRANT_COLLECTION`, `ENTERPRISE_QDRANT_COLLECTION`, and their `*_DELETE_COLLECTION` overrides against the actual collections.
- LifeStore’s MCP tool uses `LIFESTORE_QDRANT_COLLECTION` directly, while the refresh script treats it as an ingestion base. Confirm the intended collection for each workflow rather than sharing an override blindly. The LangGraph retrieval path supports `LIFESTORE_QDRANT_SEARCH_COLLECTION` as an exact collection override.
- `CLEAR_QDRANT_BEFORE_INGEST` and `DELETE_BASE_QDRANT_COLLECTION_TOO` default to `true`: refresh can delete existing collections before replacement. Back up required data and verify targets first.
- `RUN_QDRANT_INGESTIONS` and `RUN_NEO4J_GRAPH_REFRESH` default to `true`; disable the graph step when Neo4j is not configured.

Manual production refresh:

```bash
docker compose -f docker-compose.prod.yml exec -T backend python scripts/monthly_kb_refresh.py
```

Scheduling is separate from Compose startup. The existing [cron wrapper](../scripts/run_monthly_kb_refresh_prod.sh) assumes `/opt/Ask_SLT`, writes `logs/monthly_kb_refresh.log`, and calls the **default development Compose file**. Before using it for a production-file deployment, update its Compose invocations to select `docker-compose.prod.yml` and verify its readiness check and permissions.

Once the wrapper matches the deployment and is executable, the documented host cron schedule is:

```cron
0 2 1 * * /opt/Ask_SLT/scripts/run_monthly_kb_refresh_prod.sh
```

This runs at 02:00 on the first day of each month in the host’s timezone. Check the installed crontab and latest log entries; the repository cannot confirm that a job is installed. A successful script run ends with `Monthly KB refresh completed successfully.`

## Troubleshooting

| Symptom | Check |
| --- | --- |
| App cannot connect to databases | Local processes use PostgreSQL port 5433 and Qdrant port 6335. Containers use service names and internal ports 5432/6333. |
| Sign-in fails or chat returns 401 | Entra client/tenant settings, redirect URI, and bearer token validity. Backend auth is enabled by default. |
| KB results are empty or a collection is missing | Ingestion status, actual collection name, and embedding provider/model/dimensions. Explicit LifeStore collection overrides must include the correct suffix. |
| LifeStore facts, images, or descriptions are missing | MCP health, catalog JSON, Qdrant data, and Neo4j connection/data for the retrieval path in use. |
| Refresh fails | Latest backend and refresh logs, admin access, deletion targets, and graph script path. Old failures remain in append-only logs. |

For basic backend availability, request `/` on the backend port. Use `/api/v1/admin/ingestion-status` for ingestion state and the Qdrant dashboard or client to inspect collection counts. Production Qdrant is reachable only inside its Docker network by default.
