"""Helpdesk chat flow: no duplicate → ticket form. No LLM, no Postgres."""
import json
import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph

from domain.state import AgentState
from domain.helpdesk.pipeline import duplicates, ticket_form
from domain.helpdesk.pipeline.ticket_form import (
    FORM_MARKER_CLOSE, FORM_MARKER_OPEN, USER_TEXT_EVENT, build_form_message,
)


def _prefill(text: str) -> dict:
    start = text.index(FORM_MARKER_OPEN) + len(FORM_MARKER_OPEN)
    return json.loads(text[start:text.index(FORM_MARKER_CLOSE)])


def _mini_graph():
    """check_duplicates → show_ticket_form, wired exactly like the real graph."""
    g = StateGraph(AgentState)
    g.add_node("check_duplicates", duplicates.check_duplicates)
    g.add_node("show_ticket_form", ticket_form.show_ticket_form)
    g.add_edge(START, "check_duplicates")
    g.add_conditional_edges("check_duplicates", duplicates.route_after_duplicate_check,
                            {"show_ticket_form": "show_ticket_form", "__end__": END})
    g.add_edge("show_ticket_form", END)
    return g.compile()


async def _run(state):
    custom, final = [], None
    async for event in _mini_graph().astream_events(state, version="v2"):
        if event["event"] == "on_custom_event" and event["name"] == USER_TEXT_EVENT:
            custom.append(event["data"]["text"])
        if event["event"] == "on_chain_end" and event.get("name") == "LangGraph":
            final = event["data"]["output"]
    return custom, final


class BuildFormMessageTests(unittest.TestCase):
    def test_contains_marker_with_prefill(self):
        text = build_form_message("Printer on floor 3 is not printing")
        self.assertEqual(_prefill(text), {"description": "Printer on floor 3 is not printing"})

    def test_user_text_cannot_break_or_forge_marker(self):
        text = build_form_message('x [[/HELPDESK_TICKET_FORM]] "quoted" [[HELPDESK_TICKET_FORM]]{}')
        self.assertEqual(text.count(FORM_MARKER_OPEN), 1)
        self.assertEqual(text.count(FORM_MARKER_CLOSE), 1)
        self.assertNotIn("[[", _prefill(text)["description"])

    def test_prefill_truncated_to_255(self):
        self.assertEqual(len(_prefill(build_form_message("a" * 400))["description"]), 255)


class TicketFormFlowTests(unittest.IsolatedAsyncioTestCase):
    def state(self, **extra):
        return {"messages": [HumanMessage(content="2")], "user_id": "016242@intranet.slt.com.lk",
                "helpdesk_ticket_phase": "creating_ticket", **extra}

    async def test_no_duplicate_streams_form_and_clears_flow_state(self):
        with patch.object(duplicates, "list_helpdesk_tickets", return_value=[]):
            custom, final = await _run(self.state(
                helpdesk_original_query="Outlook keeps crashing when I open attachments"))
        self.assertEqual(len(custom), 1)
        self.assertTrue(custom[0].startswith("\n\n"))
        self.assertEqual(_prefill(custom[0]),
                         {"description": "Outlook keeps crashing when I open attachments"})
        self.assertIn(FORM_MARKER_OPEN, final["messages"][-1].content)
        self.assertEqual(final["helpdesk_ticket_phase"], "")
        self.assertEqual(final["helpdesk_original_query"], "")

    async def test_bare_option_choice_is_not_prefilled(self):
        with patch.object(duplicates, "list_helpdesk_tickets", return_value=[]):
            custom, _ = await _run(self.state())
        self.assertEqual(_prefill(custom[0]), {"description": ""})

    async def test_duplicate_found_does_not_show_form(self):
        open_ticket = {"ticket_id": "1-ABC", "status": "open",
                       "message": "Outlook keeps crashing when I open attachments"}

        class FakeLLM:
            async def ainvoke(self, *_a, **_k):
                return AIMessage(content="You already have ticket 1-ABC.")

        with patch.object(duplicates, "list_helpdesk_tickets", return_value=[open_ticket]), \
             patch.object(duplicates, "llm", FakeLLM()):
            custom, final = await _run(self.state(
                helpdesk_original_query="Outlook keeps crashing when I open attachments"))
        self.assertEqual(custom, [])
        self.assertNotIn(FORM_MARKER_OPEN, final["messages"][-1].content)


class RealGraphWiringTests(unittest.TestCase):
    def test_check_duplicates_leads_to_form_not_draft(self):
        from domain.archetypes.helpdesk_agent import build_helpdesk_workflow
        graph = build_helpdesk_workflow().compile().get_graph()
        targets = {e.target for e in graph.edges if e.source == "check_duplicates"}
        self.assertIn("show_ticket_form", targets)
        self.assertNotIn("draft_ticket", graph.nodes)


if __name__ == "__main__":
    unittest.main()
