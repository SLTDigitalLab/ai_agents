"""
Agent Registry - maps frontend ``agent_id`` values to LangGraph builder functions
and caches the compiled graphs.

Each agent's graph is compiled **once** and bound to a long-lived checkpointer
pool (see ``core.checkpointer``), then reused for the lifetime of the process.
This avoids rebuilding/recompiling the graph and opening a new DB pool on every
request. The earlier concern about "stale DB connections" no longer applies:
the connection pool transparently re-establishes broken connections.

Usage::

    from domain.registry import get_compiled_async_graph, get_compiled_sync_graph

    graph = await get_compiled_async_graph("askhr")   # streaming
    graph = get_compiled_sync_graph("askhr")           # history / admin
"""

import asyncio
import threading

from langgraph.graph import StateGraph

from core.checkpointer import get_sync_checkpointer, get_async_checkpointer
from domain.archetypes.kb_agent import build_kb_workflow
from domain.archetypes.kb_api_agent import build_kb_api_workflow
from domain.archetypes.kb_form_agent import build_kb_form_workflow
from domain.archetypes.kb_slm_agent import build_kb_slm_workflow
from domain.archetypes.supervisor_agent import build_supervisor_workflow

# ── Registry ─────────────────────────────────────────────────────────────
# Maps each agent_id (sent by the frontend) to the *builder function*
# that returns an uncompiled StateGraph for the appropriate archetype.
AGENT_BUILDERS: dict[str, callable] = {
    # Default supervisor agent that routes between specialists based on user needs
    "supervisor": build_supervisor_workflow,

    # Archetype 1 – Knowledge Base only
    "finance": build_kb_workflow,
    "admin": build_kb_workflow,
    "process": build_kb_workflow,
    "it": build_kb_workflow,
    "cia": build_kb_workflow,
    "network": build_kb_workflow,
    "legal": build_kb_workflow,
    "marketing": build_kb_workflow,
    "enterprise_business": build_kb_workflow,
    "consumer_business": build_kb_workflow,
    "backoffice_email": build_kb_workflow,
    "rainbowpages": build_kb_workflow,
    "aiexpo": build_kb_workflow,

    # Archetype 2 – KB + API
    "hr": build_kb_api_workflow,

    # Archetype 3 – KB + Form (Generative UI)
    "lifestore": build_kb_form_workflow,
    "enterprise": build_kb_form_workflow,

    # Archetype 4 – KB powered by internal SLM (Ollama)
    "askhrslm": build_kb_slm_workflow,
}


def get_agent_builder(agent_id: str):
    """Return the StateGraph builder function for the given agent.

    Args:
        agent_id: Identifier sent by the frontend (e.g. ``"hr"``).

    Returns:
        A callable that returns an uncompiled ``StateGraph``.

    Raises:
        ValueError: If *agent_id* is not registered.
    """
    if agent_id not in AGENT_BUILDERS:
        raise ValueError(
            f"Unknown agent_id '{agent_id}'. "
            f"Valid options: {list(AGENT_BUILDERS.keys())}"
        )

    return AGENT_BUILDERS[agent_id]


# ── Compiled graph cache ───────────────────────────────────────────────────
# Each agent is compiled once per mode (async for streaming, sync for reads)
# and bound to its long-lived checkpointer pool.

_async_graphs: dict = {}
_async_graphs_lock = asyncio.Lock()
_sync_graphs: dict = {}
_sync_graphs_lock = threading.Lock()


async def get_compiled_async_graph(agent_id: str):
    """Return the cached async-compiled graph for *agent_id*, building it once.

    Raises ValueError if the agent is unknown.
    """
    graph = _async_graphs.get(agent_id)
    if graph is not None:
        return graph

    async with _async_graphs_lock:
        graph = _async_graphs.get(agent_id)
        if graph is not None:
            return graph

        builder_fn = get_agent_builder(agent_id)  # validates agent_id
        checkpointer = await get_async_checkpointer(agent_id)
        graph = builder_fn().compile(checkpointer=checkpointer)
        _async_graphs[agent_id] = graph
        return graph


def get_compiled_sync_graph(agent_id: str):
    """Return the cached sync-compiled graph for *agent_id*, building it once.

    Raises ValueError if the agent is unknown.
    """
    graph = _sync_graphs.get(agent_id)
    if graph is not None:
        return graph

    with _sync_graphs_lock:
        graph = _sync_graphs.get(agent_id)
        if graph is not None:
            return graph

        builder_fn = get_agent_builder(agent_id)  # validates agent_id
        checkpointer = get_sync_checkpointer(agent_id)
        graph = builder_fn().compile(checkpointer=checkpointer)
        _sync_graphs[agent_id] = graph
        return graph
