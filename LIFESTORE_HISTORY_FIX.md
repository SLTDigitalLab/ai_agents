# LifeStore historical price reference regression

## Root cause and traced request

This is a routing/intent gap, not a PostgreSQL shape or persistence failure. The request "what was the cheapest router we discussed earlier?" had no dedicated history route. The planner only offers greeting, single_product, availability, category_browse, comparison, purchase, and general. Its price instructions describe current retrieval, while its previous-product instructions ask for a precise product name. Neither normalization nor _resolve_price_context distinguishes historical cheapest/most-expensive from current catalog queries.

The trace is:

1. lifestore_mcp_chat loads get_conversation_memory(thread_id) before saving the new user message. The storage service reads PostgreSQL on every call and reconstructs exactly summary, messages, and nested state (current_category, last_products_shown, last_selected_product, pending_action). The only service cache is the table-initialization flag.
2. The router passes that object as conversation_memory to _plan_lifestore_answer. The prompt includes the summary, last six recent messages, current category, numbered shown products with numeric prices/type/category, selected-product JSON, and pending action. PostgreSQL migration did not remove or rename these fields.
3. Before the fix there was no unique deterministic classification for the sentence: the actual plan depends on the LLM. The exact reproducible fallback classification is answer_mode=single_product, price_intent=none, product_query equal to the original sentence, desired_product_count=1. A planner response with single_product and an omitted/invalid price intent normalizes to the same routing behavior.
4. In that branch _resolve_price_context immediately returns because price_intent is none. _retrieve_products calls explicit_identity_query, which returns exactly "cheapest router we discussed earlier". It does NOT return only "router". This extractor removes question words but treats the remaining multiword phrase as explicit identity because none of its existing pronoun/ordinal reference guards match.
5. That extracted phrase overrides even a correct product name supplied by the planner, and reaches lifestore_precise_product_lookup. Strict identity matching correctly rejects the non-name phrase, producing the reported exact-match clarification.
6. If the planner instead emits price_intent=cheapest, the old code searches CURRENT catalog candidates. This is also the wrong source for the historical request. Merely improving the planner's cheapest classification would therefore not fix the historical semantics.

The live planner JSON/trace and exact thread ID were not supplied, so the exact live LLM classification cannot be independently asserted. The fallback/precise path and exact extracted identity above are reproduced by a regression test. The supplied live report confirms persistence after restart; local tests additionally verify the same storage shape and answer after an actual service-module reload against the mocked PostgreSQL boundary. No live DB connection or production restart was performed during this fix.

## Focused fix

After the existing order-status guard, the router now recognizes explicit historical cheapest/most-expensive questions before loading MCP or invoking the planner. It handles forms such as "what was the cheapest router we discussed earlier?", "cheapest router we discussed", and "most expensive routers you showed me". Current catalog questions and current availability/purchase requests do not use this path.

The resolver compares validated numeric stored prices using the existing product_price function (price_value first, formatted price fallback). It considers last_products_shown plus last_selected_product, filters by explicit category words in the stored name/type/category, and computes min/max deterministically. It includes tied names and reports missing prices. The answer explicitly says it is based on products still saved from the conversation and on remembered prices, avoiding claims of complete historical coverage or live prices. A separately remembered selection can recover the Prolink product even when it is absent from the latest displayed subset.

Summary prose is still supplied to the normal planner, but is not parsed into invented product references or used for LLM arithmetic. If matching structured references/prices are absent, historical recall asks which products were being compared. This includes summary-only memories: they are insufficient for this deterministic comparison path. Old product references are not emitted as current catalog cards, and historical recall leaves structured state unchanged. The outer router still records the user/assistant turn and retains existing summarization behavior.

No PostgreSQL schema/storage change, identity-rule weakening, retrieval rewrite, crawler, graph/vector rebuild, or frontend change was made.

## Exact files changed in this follow-up

- backend/services/lifestore_prices.py: historical_price_reference resolver.
- backend/routers/lifestore_mcp_chat.py: import and early history response branch.
- backend/test_lifestore_history.py: 11 regression tests.
- LIFESTORE_HISTORY_FIX.md: this report.

Existing workspace edits and prior migration files were preserved.

## Verification

93 tests pass: all 82 previous LifeStore tests and 11 new historical-reference tests. The new tests cover:

- Cheapest A / Rs. 3,800 without planner/MCP lookup, retaining state and recording the turn.
- Most expensive C / Rs. 14,980, independent of the selected cheapest product.
- Router-only filtering with lower/higher-priced speakers also remembered.
- Selected product outside the most recently shown subset.
- Missing memory, summary-only memory, and absent requested category.
- Missing prices and ties.
- Service-module/cache reset with database rows retained and the same historical answer.
- Exact nested memory shape and all restored fields in the planner prompt.
- Reproduction of the original fallback and precise-identity failure path.
- Both "what is the cheapest router?" and "show me cheapest routers" retaining current catalog retrieval and deterministic ranking.
- Current availability, purchase, and current-price requests involving a historical product not being intercepted.

PostgreSQL is mocked with the existing transactional test double; LLM/MCP boundaries are mocked where appropriate. These are regression/unit checks, not a live PostgreSQL integration run. Expected generic storage warnings and a mocked offline traceback are emitted by existing failure-path tests.

Reproduce in PowerShell:

```powershell
Set-Location D:\SLTnew\ai_agents
$env:LIFESTORE_MCP_SERVER_PATH = 'D:\SLTnew\ai_agents\mcp_lifestore\server.py'
.\.venv-cb06\Scripts\python.exe -m unittest discover -s backend -p 'test_lifestore*.py'
```

## Deployment

The existing Compose configurations bind-mount backend source. After deploying these files, restart only the backend using the same Compose project/file already running:

```powershell
Set-Location D:\SLTnew\ai_agents
docker compose -f docker-compose.yml restart backend
```

For the production Compose configuration, instead use:

```powershell
docker compose -f docker-compose.prod.yml restart backend
```

No table migration, dependency installation, or data rebuild is required. In the same existing browser thread, repeat "what was the cheapest router we discussed earlier?". With the reported structured memory, the response should name Prolink PRS1140 ADSL Router at Rs. 3,800.00, with source=conversation_memory and tool_name=null.
