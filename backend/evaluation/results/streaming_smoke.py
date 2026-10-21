"""Manual provider smoke: routing and streamed planning only; no specialists."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, START, END
from core.config import settings
from domain.state import AgentState
from domain.archetypes import supervisor_agent as supervisor


async def stream_plan(state):
    builder = StateGraph(AgentState)
    builder.add_node("decompose_query", supervisor.decompose_query)
    builder.add_edge(START, "decompose_query")
    builder.add_edge("decompose_query", END)
    graph = builder.compile()
    output = None
    chunks = 0
    async for event in graph.astream_events(state, version="v2"):
        if event["event"] == "on_chat_model_stream":
            chunks += 1
        if event["event"] == "on_chain_end" and not event.get("parent_ids"):
            output = event["data"]["output"]
    assert output is not None
    assert chunks > 0, "Expected actual model streaming"
    assert not output.get("routing_metadata", {}).get("planning_error")
    assert output.get("specialist_queries"), "No parsed plan returned"
    return {
        "stream_chunks": chunks,
        "planned_specialists": list(output["specialist_queries"]),
        "task_count": len(output.get("routing_tasks", [])),
    }


async def main():
    query = "procedure to follow when an equipment lost under my custody"
    state = {"messages": [HumanMessage(content=query)], "delegation_query": query,
             "agent_id": "supervisor", "routing_action": "multi_delegate",
             "routed_agent_id": "admin", "routed_agent_ids": ["admin", "hr"],
             "routing_metadata": {"backend": "legacy"}}
    legacy = await stream_plan(state)
    candidate = await supervisor.route_request_decisions({"messages": state["messages"]})
    candidate_summary = {"action": candidate["routing_action"],
                         "agents": candidate["routed_agent_ids"],
                         "reason": candidate["routing_reason"],
                         "probabilities": candidate["routing_probabilities"],
                         "clarification_options": candidate.get("clarification_options", [])}
    print(json.dumps({"decisions_route": candidate_summary}), flush=True)
    # Validate the new planner even if classification asks for clarification.
    # Explicitly distinguish this forced-schema smoke from automatic delegation.
    forced = candidate["routing_action"] not in ("delegate", "multi_delegate")
    plan_state = {**state, "routing_metadata": {"backend": "decisions"}} if forced else {**state, **candidate}
    planned = await stream_plan(plan_state)
    print(json.dumps({"configured_mode": settings.SUPERVISOR_ROUTING_BACKEND,
                      "chat_model": settings.LLM_MODEL, "legacy_streaming": legacy,
                      "decisions_route": candidate_summary,
                      "planner_specialists_forced_for_schema_check": forced,
                      "decisions_streaming": planned}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
