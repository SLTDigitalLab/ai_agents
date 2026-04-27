"""
Agent Registry - maps frontend ``agent_id`` values to LangGraph builder functions.

Instead of caching compiled graphs globally (which would hold stale DB connections),
this registry returns the *builder function* so that the router can compile a fresh
graph with a per-request checkpointer.

Usage::

    from domain.registry import get_agent_builder

    builder_fn = get_agent_builder("askhr")
    workflow = builder_fn()
    # verify checkpointer lifecycle in router...
"""

from langgraph.graph import StateGraph

from domain.archetypes.kb_agent import build_kb_workflow
from domain.archetypes.kb_api_agent import build_kb_api_workflow
from domain.archetypes.kb_form_agent import build_kb_form_workflow
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

    # Archetype 2 – KB + API
    "hr": build_kb_api_workflow,

    # Archetype 3 – KB + Form (Generative UI)
    "lifestore": build_kb_form_workflow,
    "enterprise": build_kb_form_workflow,
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
