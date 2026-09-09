# Sentinel setup for Workmate AI

This integration leaves the Ollama/SLM clients, models, dimensions and collections
unchanged. It implements the text-chat and embedding contract from
[SENTINEL_INTEGRATION_MANUAL.md](SENTINEL_INTEGRATION_MANUAL.md).

## What runs where

| Feature | With Sentinel enabled |
|---|---|
| Main chat answers, supervisor direct answers and multi-agent synthesis | `SENTINEL_GATEWAY_MODEL`, default `company-auto` |
| Application guardrail classifier | `SENTINEL_GUARDRAIL_MODEL`, default `company-small`; JSON text validated locally |
| Supervisor query decomposition | Sentinel text with local schema validation |
| Native tool decisions for KB, HR, Enterprise and LifeStore | Existing direct OpenAI model, configured by `LLM_MODEL`, `LLM_API_KEY`/`OPENAI_API_KEY`, `LLM_BASE_URL` |
| Document ingestion and query embeddings | Exact approved Sentinel embedding model when enabled; float vectors |
| Supervisor routing embeddings | Optional separate provider switch; Sentinel uses the configured exact embedding model |
| SLM / audio / realtime providers | Existing providers remain in use |

This is **hybrid mode**, not a complete removal of direct OpenAI calls. The manual
does not establish gateway support for native tools. The existing direct planner
continues its tool loop; after its final draft, Sentinel writes the final answer.
This adds a generation request and can increase latency/cost. Planner drafts are
not streamed or saved as final answers. Existing tools, cart/payment validation,
graph checkpoints, user identity, citations and frontend response format remain.
Sentinel text responses arrive as a completed answer rather than token by token.

Do not replace `OPENAI_API_KEY` with the Sentinel virtual key. Do not set the
direct planner's `LLM_MODEL` to `company-auto`. A full tool migration requires a
separately documented and tested gateway tool contract.

## 1. Prepare the backend configuration

From the repository root in PowerShell:

```powershell
Copy-Item deploy/sentinel.env.example .env.sentinel
```

Fill in `.env.sentinel` locally. This git-ignored file is loaded by Python before
the existing `.env`; environment variables supplied by the process take priority.
Leave your existing direct OpenAI and SLM settings in `.env`.

Required for chat and guardrails:

```dotenv
SENTINEL_GATEWAY_URL=https://sentinel.raccoon-ai.io
SENTINEL_GATEWAY_API_KEY=YOUR_VIRTUAL_KEY
SENTINEL_GATEWAY_MODEL=company-auto
SENTINEL_GUARDRAIL_MODEL=company-small
LLM_PROVIDER=sentinel
GUARDRAIL_PROVIDER=sentinel
```

The real key belongs only in local/backend secret configuration. Never add it to
a `VITE_*` variable, frontend source, Git, or chat. The Sentinel application/key
must allow `/v1/chat/completions`, the chosen aliases, and have sufficient budget.
`company-auto` quality depends on the models configured in its Small/Medium/High
groups. Evaluate your actual department questions and tool-derived answers.

The classifier uses text JSON, not gateway `response_format`, native tools, or
Sentinel managed guardrails. Invalid/truncated classifier output or gateway failure
returns an unavailable error; it does not silently classify the input as PASS.
Other existing providers retain their previous failure behavior.

The example disables remote prompt tracing. Keep those settings while evaluating
internal documents. No gateway retries are automatic; 429 rate/budget failures
are reported, not retried. Request IDs are captured without logging gateway
prompt/response bodies. `SENTINEL_GATEWAY_TIMEOUT_MS=130000` is a per-request
setting; tune `VOICE_CHAT_TIMEOUT_SECONDS` to the total multi-call workflow deadline.

## 2. Start with chat and guardrails

Keep `EMBEDDING_PROVIDER=openai`, `ROUTING_EMBEDDING_PROVIDER=openai`, and
`SENTINEL_STAGE_EMBEDDINGS=false` initially. Empty embedding settings are allowed
while embedding features are disabled. Restart the Python backend after changes.

Docker development (run from repository root):

```powershell
docker compose -f docker-compose.yml -f deploy/compose.sentinel.dev.yml up -d --build backend mcp_lifestore
```

Production: place `.env.sentinel` alongside the deployment `.env`, then:

```bash
docker compose -f docker-compose.prod.yml -f deploy/compose.sentinel.prod.yml up -d --build backend mcp-lifestore
```

`WORKMATE_SENTINEL_ENV_FILE` overrides the default production path
`/root/slt-app/.env.sentinel`. The override adds the file only to backend and MCP
containers, not the frontend. Use the same compose override on future recreations.

## 3. Verify endpoint access

Use the backend Python environment with its dependencies installed:

```powershell
python backend/scripts/check_sentinel.py
python backend/scripts/check_sentinel.py --chat --guardrail
```

The first command only discovers allowed models. Each flag in the second sends
one small synthetic generation request and can incur usage. In Docker, execute
`python scripts/check_sentinel.py ...` inside the backend container. Do not claim
success until both desired aliases return valid responses.

## 4. Configure embeddings and choose a migration path

Obtain the **exact public model name and dimensions** from the Sentinel operator.
For continuity, request the same underlying `text-embedding-3-large` model with
3072 dimensions as the existing OpenAI configuration. Its public name may differ;
do not assume the key grants access to that name. The key must also permit
`/v1/embeddings`.

Set:

```dotenv
SENTINEL_EMBEDDING_MODEL=EXACT_APPROVED_PUBLIC_NAME
SENTINEL_EMBEDDING_DIMENSIONS=3072
```

Use 3072 only if the operator confirms it. No dimension override is sent to the
gateway. The configured dimension validates responses and creates Qdrant vectors.

```powershell
python backend/scripts/check_sentinel.py --embeddings --rag
```

The optional `--rag` check embeds two synthetic documents plus one query and
searches an in-memory Qdrant collection. It does not access the active index.

### Path A: build isolated collections (default)

Keep query embeddings on OpenAI while building the new index:

```dotenv
EMBEDDING_PROVIDER=openai
SENTINEL_STAGE_EMBEDDINGS=true
SENTINEL_REUSE_EXISTING_VECTORS=false
SENTINEL_COLLECTION_PREFIX=sentinel_v1_
CLEAR_QDRANT_BEFORE_INGEST=false
```

Restart backend/MCP and run the existing admin document/URL ingestion for each
department with the same logical agent names. The ingestion factory uses Sentinel;
queries still use the old OpenAI collections. New physical names contain the prefix,
a fingerprint of model/dimensions/chunking version, and the existing collection name
(for example `sentinel_v1_<fingerprint>_finance_docs`). The check script prints
the finance ingestion/query targets. Do not pass the physical name as an agent ID.

Chunks retain source metadata and add embedding identity, dimensions, chunking
version and stable chunk IDs. Repeated identical chunks upsert rather than duplicate.
The old collections remain available for rollback. The legacy monthly script's
delete-before-ingest operation is blocked in Sentinel/staging mode; use
`CLEAR_QDRANT_BEFORE_INGEST=false`. Existing OneDrive refresh behavior for changed
files still applies inside the selected target collection.

After all required collections are populated and validated, cut over:

```dotenv
EMBEDDING_PROVIDER=sentinel
SENTINEL_STAGE_EMBEDDINGS=false
```

Restart backend and MCP together. Check a known document through the existing chat
and external retrieval endpoints, including source citations and access filters.
To roll back query embeddings, restore `EMBEDDING_PROVIDER=openai` and restart;
keep the original OpenAI embedding model and dimensions in `.env`.

### Path B: reuse the existing vectors (only after explicit compatibility confirmation)

If the operator confirms the exact same underlying embedding model, dimensions
and vector space as the existing index, you can use:

```dotenv
EMBEDDING_PROVIDER=sentinel
SENTINEL_STAGE_EMBEDDINGS=false
SENTINEL_REUSE_EXISTING_VECTORS=true
```

The code requires `SENTINEL_EMBEDDING_DIMENSIONS` to equal the existing
`EMBEDDING_DIMENSIONS`. That check cannot prove vector-space compatibility; equal
length alone is insufficient. Verify retrieval against known indexed documents.

To compare direct OpenAI and Sentinel vectors for the same synthetic text, then
compare their ranked results in existing Qdrant collections without writing data:

```powershell
python backend/scripts/check_sentinel_compatibility.py
```

This uses the original `EMBEDDING_MODEL` and direct key from `.env` as the reference.
It never sends stored document content to either provider. Exit status 0 means
both checks passed, 1 means the comparison failed, and 2 means embedding vectors
matched but an existing compatible collection was unavailable for verification.
When `KB_REMOTE_URL` is enabled, run this check on the remote backend with its own
Qdrant connection; local settings do not migrate that server.

Optional routing migration: set `ROUTING_EMBEDDING_PROVIDER=sentinel`. This uses
the same approved Sentinel embedding model for both routing profiles and queries;
it may be larger/more expensive than the old `text-embedding-3-small`. Restart to
rebuild cached profiles and evaluate specialist routing before enabling widely.

If `KB_REMOTE_URL` is set, local RAG delegates to that remote Workmate instance;
its query embedding configuration controls retrieval. Coordinate its cutover too.

## Validation and current limits

Offline tests: `python -m pytest backend/tests/test_sentinel.py backend/tests/test_voice_proxy.py`.
Gateway live checks require your configured key and are not implied by mocked tests.
The optional separate `routers/lifestore_mcp_chat.py` router is not mounted by
`main.py`; its legacy direct OpenAI writer has not been migrated. The active
LifeStore graph uses hybrid mode described above. Audio/realtime API calls remain
direct and still need their provider credentials.
