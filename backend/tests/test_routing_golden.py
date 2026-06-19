"""Golden-set routing regression tests for the supervisor.

These call `route_request`, which embeds the query and scores it against the
specialist routing profiles — so they need the embedding API. They auto-skip
when it is unreachable (no key / offline), keeping the default suite green
everywhere while still guarding routing behaviour where credentials exist.

Run only these:        pytest -m routing
Skip these:            pytest -m "not routing"

The most important guard here is `test_tender_query_reaches_finance` — the
regression that started this whole investigation (the tender query must always
reach the finance specialist, whether delegated alone or via fan-out).
"""

import pytest
from langchain_core.messages import HumanMessage

from domain.archetypes import supervisor_agent as sup

pytestmark = pytest.mark.routing


@pytest.fixture(scope="module")
def routing_ready():
    """Skip the module unless the routing embedding model actually responds."""
    try:
        from core.llm import get_routing_embedding_model

        get_routing_embedding_model().embed_query("ping")
    except Exception as exc:  # noqa: BLE001 - any failure means "can't run routing tests"
        pytest.skip(f"routing embeddings unavailable: {type(exc).__name__}: {exc}")


def _routed_agents(result: dict) -> set[str]:
    """Normalise single-delegate and multi-delegate results to a set of ids."""
    if result.get("routed_agent_ids"):
        return set(result["routed_agent_ids"])
    if result.get("routed_agent_id"):
        return {result["routed_agent_id"]}
    return set()


async def _route(query: str) -> dict:
    return await sup.route_request({"messages": [HumanMessage(content=query)]})


@pytest.mark.parametrize("query,expected_agent", [
    ("How do I apply for annual leave in the ERP system?", "hr"),
    ("My domain password expired and my account is locked, how do I reset it?", "it"),
    ("What is the price of the FTTH New Connection Standard package?", "consumer_business"),
    ("What are the role and responsibilities of MDRC?", "finance"),
])
async def test_clear_specialist_queries_route_to_expected_agent(routing_ready, query, expected_agent):
    result = await _route(query)
    assert expected_agent in _routed_agents(result), (
        f"{query!r} → action={result.get('routing_action')} "
        f"agents={_routed_agents(result)}; expected {expected_agent}"
    )


async def test_tender_query_reaches_finance(routing_ready):
    """The originating regression: 'call tenders' must always reach finance."""
    result = await _route("What is the procedure to call tenders?")
    assert "finance" in _routed_agents(result), (
        f"tender query must reach finance → action={result.get('routing_action')} "
        f"agents={_routed_agents(result)} scores={result.get('routing_scores')}"
    )


async def test_out_of_scope_query_is_not_delegated(routing_ready):
    """A clearly unrelated question must not be confidently sent to a specialist."""
    result = await _route("What is the capital of France?")
    assert result.get("routing_action") != "delegate", (
        f"out-of-scope query was delegated → {result.get('routing_action')} "
        f"{_routed_agents(result)} scores={result.get('routing_scores')}"
    )
