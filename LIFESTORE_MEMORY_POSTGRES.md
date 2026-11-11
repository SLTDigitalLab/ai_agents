# LifeStore PostgreSQL storage migration

## Files changed for this task

- backend/services/lifestore_memory.py
- backend/lifestore_memory_test_support.py (new transactional psycopg test double)
- backend/test_lifestore_memory.py (10 new storage contract tests)
- backend/test_lifestore_prices.py (mock database setup)
- backend/test_lifestore_availability.py (mock database setup)
- backend/test_lifestore_orders.py (mock database setup)
- backend/test_lifestore_multi_intent.py (mock database setup)
- LIFESTORE_MEMORY_POSTGRES.md (this report)

Other pre-existing workspace changes were left intact.

## Storage and behavior

The initializer creates public.lifestore_memory in the database selected by the existing settings.POSTGRES_URL (slt_db in Compose). It creates the requested columns, defaults and composite primary key (agent_id, thread_id), plus public.idx_lifestore_memory_updated_at on updated_at. Both CREATE statements are idempotent. No live database was changed during implementation; initialization runs on the first valid memory access or via the explicit command below.

| Former dictionary field | PostgreSQL column |
| --- | --- |
| dictionary key | thread_id |
| implicit LifeStore identity | agent_id = ask_lifestore, matching LifeStoreMCPChatRequest |
| summary | summary TEXT |
| messages | messages JSONB |
| state.current_category | current_category TEXT |
| state.last_products_shown | last_products_shown JSONB |
| state.last_selected_product | last_selected_product JSONB, nullable |
| state.pending_action | pending_action TEXT |
| new metadata | created_at and updated_at TIMESTAMPTZ |

_CONVERSATIONS is completely removed. Only successful table initialization is cached; every memory read comes from PostgreSQL. Public signatures and nested return shape remain unchanged, including clear_conversation. Missing/empty thread IDs never connect or create rows. Messages retain the existing last-20 limit; summarization still triggers above 14 messages and keeps 6 by default. Product references retain the same seven fields and eight-product cap. Multi-product results clear stale selection; a single result updates selection without replacing the prior list.

Each mutation inserts the row if absent, acquires SELECT FOR UPDATE, reads and updates inside one psycopg transaction. Concurrent writers serialize per conversation, preserving appended messages within the existing retention limit. Summary and messages commit together without changing structured state. Messages appended during LLM summarization are retained when applying the snapshot; a snapshot whose recent messages no longer match is ignored so it cannot overwrite newer history. Initial DDL uses a transaction advisory lock across workers and a local thread lock.

Database errors roll back the transaction, log a generic warning, and raise MemoryStorageError with a generic message and suppressed underlying exception chain. There is no local fallback, no fabricated empty memory after failure, and failed initialization is retried. Suppressing the chain matters because the existing router returns formatted exceptions. A failed operation leaves already committed memory intact.

## Verification

82 tests passed: 10 new storage tests plus 72 existing price, identity, availability, image, order-status and multi-intent tests. New tests cover default shape, detached reads, ordered persistence, retention, simulated initialization-cache reset, category and compact product state, selection clearing, pending action, summary thresholds and payloads, atomic summary application, intervening append, invalidated summary snapshots, missing IDs, thread isolation, delete, concurrent writes, rollback, sanitized errors, and initializer retry.

The database boundary is a transactional test double. These are unit tests, not proof of live PostgreSQL locking or a real container restart. No production DB, crawler, or product data was used. Existing requirements were unchanged; missing psycopg and fastapi-mail packages were installed only into the local test environment.

Run from PowerShell:

```powershell
Set-Location D:\SLTnew\ai_agents
$env:LIFESTORE_MCP_SERVER_PATH = 'D:\SLTnew\ai_agents\mcp_lifestore\server.py'
.\.venv-cb06\Scripts\python.exe -m unittest discover -s backend -p 'test_lifestore*.py'
```

## Deployment

With the existing Compose database and backend already running, the backend source is bind-mounted in both Compose configurations. No dependency changes or image rebuild are required:

```powershell
Set-Location D:\SLTnew\ai_agents
docker compose -f docker-compose.yml restart backend
docker compose -f docker-compose.yml exec backend python -c "from services.lifestore_memory import ensure_memory_table; ensure_memory_table()"
```

For a deployment using the production Compose file, use its existing project directory and these commands instead:

```powershell
docker compose -f docker-compose.prod.yml restart backend
docker compose -f docker-compose.prod.yml exec backend python -c "from services.lifestore_memory import ensure_memory_table; ensure_memory_table()"
```

The explicit initializer is optional: first access with a nonempty thread ID initializes the table automatically. Use the same Compose file/project as the running deployment. No checkpointer, catalog, order, vector or graph tables are touched.

## psql verification

These commands use the configured PostgreSQL container and slt_db:

```powershell
docker exec slt_postgres psql -U slt -d slt_db -c '\d+ public.lifestore_memory'
docker exec slt_postgres psql -U slt -d slt_db -c "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' AND tablename = 'lifestore_memory';"
docker exec slt_postgres psql -U slt -d slt_db -x -c "SELECT agent_id, thread_id, summary, jsonb_pretty(messages) AS messages, current_category, jsonb_pretty(last_products_shown) AS last_products_shown, last_selected_product, pending_action, created_at, updated_at FROM public.lifestore_memory WHERE agent_id = 'ask_lifestore' ORDER BY updated_at DESC LIMIT 1;"
```

After chatting with a nonempty thread ID, inspect that exact row (replace YOUR_THREAD_ID):

```powershell
docker exec slt_postgres psql -U slt -d slt_db -x -c "SELECT * FROM public.lifestore_memory WHERE agent_id = 'ask_lifestore' AND thread_id = 'YOUR_THREAD_ID';"
```

Restart the backend with the command above, repeat the query, then continue chatting with the same thread ID to verify persisted context.
