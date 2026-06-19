# Regression suite

Guards the supervisor routing + answer-handling logic so prompt/routing changes
don't silently regress (the failure mode we kept hitting by eyeballing chats).

## Setup
```bash
pip install -r requirements-dev.txt
```

## Run (from `backend/`)
```bash
pytest                    # everything (routing tests auto-skip if the API is unreachable)
pytest -m "not routing"   # fast, fully offline — no network/API key needed
pytest -m routing         # only the embedding-backed golden routing tests
```

## Layers
- **`test_routing_rules.py`** — pure, network-free logic: greeting/help/vague
  detection, clarification resolution, keyword matching, decline detection,
  text normalisation. Fast; run on every change.
- **`test_multi_delegate_fanout.py`** — locks in "Fix A": a single-specialist
  decomposition still runs the full fan-out (the specialist graphs are stubbed,
  so this is deterministic and offline).
- **`test_routing_golden.py`** — `@pytest.mark.routing`. Golden query → expected
  specialist, including `test_tender_query_reaches_finance` (the original
  regression). Calls the embedding API; auto-skips when it's unavailable.

## Adding cases
When a real query routes/answers wrong, add it here **before** fixing it — the
failing test documents the bug and proves the fix. Plain-logic cases go in
`test_routing_rules.py`; "which specialist should this reach" cases go in
`test_routing_golden.py`.
