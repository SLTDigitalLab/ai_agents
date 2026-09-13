from langchain_core.messages import HumanMessage

from domain.archetypes.supervisor_agent import _is_short_follow_up, route_request


def test_then_is_a_short_follow_up():
    assert _is_short_follow_up("then")
    assert _is_short_follow_up("and then?")
    assert _is_short_follow_up("go on")


async def test_short_follow_up_reuses_previous_specialist_without_scoring():
    result = await route_request(
        {
            "messages": [HumanMessage(content="then")],
            "last_specialist_agent": "hr",
        }
    )

    assert result["routing_action"] == "delegate"
    assert result["routed_agent_id"] == "hr"
    assert result["routing_scores"] == {}
