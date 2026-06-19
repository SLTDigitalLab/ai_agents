"""Regression tests for the supervisor multi-delegate fan-out selection logic.

This locks in "Fix A": when the decomposer collapses an ambiguous query onto a
SINGLE specialist, the supervisor must still run ALL fanned-out specialists with
the full query (preserving the router's ambiguity safety net) rather than
trusting the single guess and silently dropping the router's other candidate.

Genuine compound splits (2+ specialists assigned) must still be honoured
selectively. The real specialist graphs (LLM calls) are stubbed out, so these
tests are deterministic and network-free — they exercise only the invocation
selection in `multi_delegate`.
"""

import pytest
from langchain_core.messages import HumanMessage

from domain.archetypes import supervisor_agent as sup

TENDER_Q = "What is the procedure to call tenders?"


@pytest.fixture
def recorded_invocations(monkeypatch):
    """Stub the per-specialist invocation and record (agent_id, query) calls."""
    calls: list[tuple[str, str]] = []

    async def fake_invoke(agent_id, base_state, delegation_query):
        calls.append((agent_id, delegation_query))
        return agent_id, f"answer from {agent_id}"

    monkeypatch.setattr(sup, "_invoke_specialist_for_fan_out", fake_invoke)
    return calls


async def test_single_specialist_decomposition_runs_full_fanout(recorded_invocations):
    # Router fanned out to two; decomposer collapsed onto just enterprise_business.
    state = {
        "messages": [HumanMessage(content=TENDER_Q)],
        "routed_agent_ids": ["finance", "enterprise_business"],
        "specialist_queries": {"enterprise_business": TENDER_Q},
        "delegation_query": TENDER_Q,
    }

    await sup.multi_delegate(state)

    invoked = {agent for agent, _ in recorded_invocations}
    # Both must run despite the single-specialist decomposition (the bug was that
    # finance got skipped, leaving only enterprise_business to decline).
    assert invoked == {"finance", "enterprise_business"}
    # Both receive the full query (fallback), not just the one decomposed sub-query.
    assert all(query == TENDER_Q for _, query in recorded_invocations)


async def test_empty_decomposition_runs_full_fanout(recorded_invocations):
    state = {
        "messages": [HumanMessage(content=TENDER_Q)],
        "routed_agent_ids": ["finance", "enterprise_business"],
        "specialist_queries": {},
        "delegation_query": TENDER_Q,
    }

    await sup.multi_delegate(state)

    invoked = {agent for agent, _ in recorded_invocations}
    assert invoked == {"finance", "enterprise_business"}
    assert all(query == TENDER_Q for _, query in recorded_invocations)


async def test_compound_decomposition_is_selective(recorded_invocations):
    # Genuine two-topic query: each specialist gets its own sub-question.
    state = {
        "messages": [HumanMessage(content="leave balance and an expense claim")],
        "routed_agent_ids": ["finance", "hr"],
        "specialist_queries": {
            "finance": "how do I file an expense claim?",
            "hr": "what is my leave balance?",
        },
        "delegation_query": "leave balance and an expense claim",
    }

    await sup.multi_delegate(state)

    invoked = dict(recorded_invocations)
    assert invoked == {
        "finance": "how do I file an expense claim?",
        "hr": "what is my leave balance?",
    }


async def test_compound_decomposition_skips_unassigned_specialist(recorded_invocations):
    # 2 specialists assigned, a third routed-but-unassigned must be skipped.
    state = {
        "messages": [HumanMessage(content="leave and expense claim")],
        "routed_agent_ids": ["finance", "hr", "it"],
        "specialist_queries": {
            "finance": "expense claim?",
            "hr": "leave balance?",
        },
        "delegation_query": "leave and expense claim",
    }

    await sup.multi_delegate(state)

    invoked = {agent for agent, _ in recorded_invocations}
    assert invoked == {"finance", "hr"}
    assert "it" not in invoked
