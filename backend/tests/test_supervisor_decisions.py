"""Network-free provider contract, routing, task execution and metric regressions."""
import asyncio
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from domain.routing import decisions
from domain.routing.evaluation import compare, score_case
from domain.routing.observability import emit_event, query_preview
from domain.archetypes import supervisor_agent as supervisor


def config(**overrides):
    return SimpleNamespace(**{
        "SUPERVISOR_DECISIONS_API_KEY": "synthetic", "OPENAI_API_KEY": None,
        "SUPERVISOR_DECISIONS_MODEL": "gpt-6-luna", "SUPERVISOR_DECISIONS_TIMEOUT_SECONDS": .2,
        "SUPERVISOR_DECISIONS_THRESHOLD": .8, "SUPERVISOR_DECISIONS_UNCERTAIN_THRESHOLD": .4,
        **overrides,
    })


def prediction(**values):
    p = {q["name"]: .01 for q in decisions.routing_questions()}
    p.update(values)
    return decisions.DecisionResult(p, "gpt-6-luna")


def route(action="delegate", agents=None):
    agents = agents or ["hr"]
    return {"routing_action": action, "routed_agent_ids": agents, "routed_agent_id": agents[0],
            "routing_metadata": {"backend": "decisions", "fallback": False}, "delegation_query": "Synthetic request"}


def task(task_id, agent, question="A focused synthetic policy question", depends_on=None):
    return {"task_id": task_id, "specialist_id": agent, "question": question, "depends_on": depends_on or []}


class DecisionsContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_streamed_planners_send_strict_nested_schemas_and_parse_results(self):
        from langchain_openai import ChatOpenAI

        def assert_strict(schema):
            if isinstance(schema, dict):
                if schema.get("type") == "object":
                    self.assertIs(schema.get("additionalProperties"), False)
                    self.assertEqual(set(schema.get("required", [])), set(schema.get("properties", {})))
                for value in schema.values():
                    assert_strict(value)
            elif isinstance(schema, list):
                for value in schema:
                    assert_strict(value)

        outputs = [
            {"sub_queries": [{"specialist_id": "admin", "sub_question": "Report lost equipment"}]},
            {"tasks": [task("q1", "admin", "Report lost equipment")], "unassigned_questions": []},
        ]
        requests = []

        def respond(request):
            payload = json.loads(request.content)
            requests.append(payload)
            self.assertTrue(payload["stream"])
            fmt = payload["text"]["format"]
            self.assertEqual(fmt["type"], "json_schema")
            self.assertIs(fmt["strict"], True)
            assert_strict(fmt["schema"])
            answer = json.dumps(outputs[len(requests) - 1])
            response = {
                "id": "resp_synthetic", "object": "response", "created_at": 0,
                "status": "completed", "model": "gpt-5.6-luna", "error": None,
                "output": [{"id": "msg_synthetic", "type": "message", "role": "assistant",
                            "status": "completed", "content": [{"type": "output_text", "text": answer, "annotations": []}]}],
                "text": {"format": fmt}, "usage": None, "service_tier": "default",
            }
            events = [
                {"type": "response.created", "response": {**response, "status": "in_progress", "output": []}, "sequence_number": 0},
                {"type": "response.output_text.delta", "item_id": "msg_synthetic", "output_index": 0,
                 "content_index": 0, "delta": answer, "sequence_number": 1},
                {"type": "response.output_text.done", "item_id": "msg_synthetic", "output_index": 0,
                 "content_index": 0, "text": answer, "sequence_number": 2},
                {"type": "response.completed", "response": response, "sequence_number": 3},
            ]
            content = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events)
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=content)

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            model = ChatOpenAI(model="gpt-5.6-luna", api_key="synthetic", use_responses_api=True,
                               streaming=True, max_retries=0, http_async_client=client)
            supervisor._get_routing_planner.cache_clear()
            self.addCleanup(supervisor._get_routing_planner.cache_clear)
            with patch.object(supervisor, "get_chat_model", return_value=model), \
                 patch.object(supervisor.settings, "LLM_PROVIDER", "openai"), \
                 patch.object(supervisor, "_decomposer_llm", None):
                for factory, expected in zip((supervisor._get_decomposer_llm, supervisor._get_routing_planner), outputs):
                    result = await factory().ainvoke("How do I report lost equipment?")
                    self.assertEqual(result.model_dump(), expected)
        self.assertEqual(len(requests), 2)

    async def test_real_http_contract_masks_context_and_excludes_tool_results_and_identity(self):
        def respond(request):
            self.assertEqual(str(request.url), decisions.ENDPOINT)
            self.assertEqual(request.headers["Authorization"], "Bearer synthetic")
            payload = json.loads(request.content)
            self.assertEqual(payload["model"], "gpt-6-luna")
            self.assertEqual(len(payload["questions"]), 14)
            evidence = payload["input"]
            for private in ("owner@example.test", "0771234567", "PRIVATE_HR_BALANCE", "user-identity"):
                self.assertNotIn(private, evidence)
            self.assertIn("[EMAIL]", evidence)
            return httpx.Response(200, json={"model": "gpt-6-luna", "answers": [
                {"name": q["name"], "type": "predicate", "probability": .95 if q["name"] == "hr" else .01}
                for q in payload["questions"]], "usage": {"input_tokens": 100, "private": "omit"}})
        state = {"messages": [HumanMessage(content="Email owner@example.test about leave"),
                              AIMessage(content="PRIVATE_HR_BALANCE"), HumanMessage(content="Call 0771234567")],
                 "user_id": "user-identity", "last_specialist_agent": "hr"}
        result = await decisions.classify_departments(state, "Call 0771234567", config(), transport=httpx.MockTransport(respond))
        self.assertEqual(result.probabilities["hr"], .95)
        self.assertEqual(result.usage, {"input_tokens": 100})
        self.assertEqual(len(result.prompt_hash), 16)

    async def test_http_failures_are_sanitized_and_do_not_retry(self):
        calls = []
        def respond(request):
            calls.append(request)
            return httpx.Response(429, text="PRIVATE_BODY sk-secret")
        with self.assertRaisesRegex(decisions.DecisionsError, "^http_429$"):
            await decisions.classify_departments({}, "query", config(), transport=httpx.MockTransport(respond))
        self.assertEqual(len(calls), 1)

    async def test_wall_clock_timeout_is_bounded(self):
        async def respond(request):
            await asyncio.sleep(.1)
            return httpx.Response(200, json={})
        with self.assertRaisesRegex(decisions.DecisionsError, "TimeoutError"):
            await decisions.classify_departments({}, "query", config(SUPERVISOR_DECISIONS_TIMEOUT_SECONDS=.01),
                                                 transport=httpx.MockTransport(respond))

    async def test_missing_key_never_calls_network(self):
        with self.assertRaisesRegex(decisions.DecisionsError, "missing_api_key"):
            await decisions.classify_departments({}, "query", config(SUPERVISOR_DECISIONS_API_KEY=None))

    def test_malformed_missing_refused_and_nonfinite_answers_are_rejected(self):
        for answer in ({"name":"hr", "type":"refusal"}, {"name":"hr", "type":"predicate", "probability":True},
                       {"name":"hr", "type":"predicate", "probability":float("nan")},
                       {"name":"hr", "type":"predicate", "probability":1.1},
                       {"name":"unknown", "type":"predicate", "probability":.9}):
            with self.subTest(answer=answer), self.assertRaises(decisions.DecisionsError):
                decisions.parse_result({"model":"test", "answers":[answer]}, ["hr"])
        for answers in ([], [{"name":"hr", "type":"predicate", "probability":.9}] * 2):
            with self.assertRaises(decisions.DecisionsError):
                decisions.parse_result({"model":"test", "answers":answers}, ["hr"])

    def test_three_or_more_departments_do_not_compete_for_probability_mass(self):
        result = decisions.routing_policy(prediction(hr=.99, admin=.98, it=.96, finance=.94), "query", config())
        self.assertEqual(result["routing_action"], "multi_delegate")
        self.assertEqual(set(result["routed_agent_ids"]), {"hr", "admin", "it", "finance"})
        self.assertEqual(result["routing_scores"], {})

    def test_uncertainty_and_incidental_topic_do_not_trigger_fanout(self):
        for result in (prediction(hr=.55, finance=.52),
                       prediction(hr=.97, needs_clarification=.85), prediction(hr=.99, general_help=.99)):
            actual = decisions.routing_policy(result, "query", config())
            self.assertEqual(actual["routing_action"], "clarify")
            self.assertFalse(actual["routed_agent_ids"])
        actual = decisions.routing_policy(prediction(hr=.99, finance=.03), "query", config())
        self.assertEqual(actual["routed_agent_ids"], ["hr"])
        actual = decisions.routing_policy(prediction(enterprise_business=.99, network=.74), "Corporate connectivity offering", config())
        self.assertEqual(actual["routed_agent_ids"], ["enterprise_business"])

    def test_no_department_is_not_automatically_out_of_scope(self):
        self.assertEqual(decisions.routing_policy(prediction(), "query", config())["routing_action"], "clarify")
        self.assertEqual(decisions.routing_policy(prediction(out_of_scope=.99), "query", config())["routing_action"], "out_of_scope")
        self.assertEqual(decisions.routing_policy(prediction(general_help=.99), "query", config())["routing_action"], "direct")

    def test_uncertain_missing_answer_details_do_not_veto_clear_department_ownership(self):
        result = decisions.routing_policy(prediction(hr=1, needs_clarification=.53), "Medical benefits documents", config())
        self.assertEqual(result["routing_action"], "delegate")


class SupervisorIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mode = patch.object(supervisor.settings, "SUPERVISOR_ROUTING_BACKEND", "decisions")
        self.mode.start()
        self.addCleanup(self.mode.stop)
        self.state = {"messages":[HumanMessage(content="Tell me about leave and transport")], "agent_id":"supervisor"}

    async def test_substantive_question_after_thanks_reaches_provider(self):
        self.state["messages"] = [HumanMessage(content="Thanks! How do I apply for annual leave?")]
        with patch.object(supervisor, "classify_departments", AsyncMock(return_value=prediction(hr=.99))) as classify:
            result = await supervisor.route_request_decisions(self.state)
        classify.assert_awaited_once()
        self.assertEqual(result["routed_agent_id"], "hr")

    async def test_fallback_is_visible_and_previous_turn_fields_are_cleared(self):
        self.state.update({"routing_tasks":[task("old", "it")], "routing_task_answers":{"old":"old"},
                           "routed_agent_ids":["it","admin"]})
        with patch.object(supervisor, "route_request_decisions", AsyncMock(side_effect=decisions.DecisionsError("http_503"))), \
             patch.object(supervisor, "route_request_legacy", AsyncMock(return_value={"routing_action":"delegate", "routed_agent_id":"hr"})):
            result = await supervisor.route_request(self.state)
        self.assertTrue(result["routing_metadata"]["fallback"])
        self.assertEqual(result["routing_metadata"]["backend"], "legacy")
        self.assertEqual(result["routing_tasks"], [])
        self.assertEqual(result["routed_agent_ids"], [])

    async def test_double_provider_failure_clarifies_instead_of_dropping_request(self):
        with patch.object(supervisor, "route_request_decisions", AsyncMock(side_effect=RuntimeError)), \
             patch.object(supervisor, "route_request_legacy", AsyncMock(side_effect=RuntimeError)):
            result = await supervisor.route_request(self.state)
        self.assertEqual(result["routing_action"], "clarify")
        self.assertTrue(result["routing_metadata"]["fallback"])

    async def test_shadow_returns_only_legacy_and_does_not_persist_candidate_tasks(self):
        with patch.object(supervisor.settings, "SUPERVISOR_ROUTING_BACKEND", "shadow"), \
             patch.object(supervisor, "route_request_legacy", AsyncMock(return_value={"routing_action":"delegate", "routed_agent_id":"hr"})), \
             patch.object(supervisor, "route_request_decisions", AsyncMock(return_value=route("multi_delegate", ["it","admin","finance"]))):
            result = await supervisor.route_request(self.state)
        self.assertEqual(result["routed_agent_id"], "hr")
        self.assertEqual(result["routing_metadata"]["shadow"]["agents"], ["it","admin","finance"])
        self.assertEqual(result["routing_tasks"], [])

    async def test_clarification_resumes_original_question_and_greeting_resets_context(self):
        state = {"messages":[HumanMessage(content="HR please")], "pending_clarification":True,
                 "clarification_options":["hr","finance"], "original_query":"Staff loan eligibility"}
        result = await supervisor.route_request(state)
        self.assertEqual(result["delegation_query"], "Staff loan eligibility")
        self.assertFalse(result["pending_clarification"])
        result = await supervisor.route_request({"messages":[HumanMessage(content="Hello")], "last_specialist_agents":["hr","admin"]})
        self.assertEqual(result["last_specialist_agents"], [])

    async def test_clarification_does_not_drop_already_certain_department(self):
        state = {"messages":[HumanMessage(content="Finance please")], "pending_clarification":True,
                 "clarification_options":["finance","consumer_business"], "routing_pending_agents":["hr"],
                 "original_query":"Explain staff loan eligibility and my payment issue"}
        result = await supervisor.route_request(state)
        self.assertEqual(result["routing_action"], "multi_delegate")
        self.assertEqual(result["routed_agent_ids"], ["hr","finance"])
        self.assertEqual(result["routing_pending_agents"], [])

    def test_invalid_plans_cannot_silently_prune_specialists_or_dependencies(self):
        for tasks in ([task("q1","hr")], [task("q1","hr"),task("q1","admin")],
                      [task("q1","hr",depends_on=["missing"]),task("q2","admin")],
                      [task("q1","hr",depends_on=["q2"]),task("q2","admin",depends_on=["q1"]) ]):
            with self.assertRaises(ValueError):
                supervisor._validate_routing_plan(supervisor._RoutingPlan(tasks=tasks, unassigned_questions=[]), ["hr","admin"])

    async def test_invalid_generated_plan_executes_nothing_and_records_unanswered_request(self):
        state = {**self.state, **route("multi_delegate", ["hr","admin","it"])}
        planner = SimpleNamespace(ainvoke=AsyncMock(return_value=supervisor._RoutingPlan(tasks=[task("q1","hr")], unassigned_questions=[])))
        with patch.object(supervisor, "_get_routing_planner", return_value=planner), \
             patch.object(supervisor, "_invoke_specialist_for_fan_out", AsyncMock()) as invoke:
            plan = await supervisor.decompose_query(state)
            execution = await supervisor.multi_delegate({**state, **plan})
            final = await supervisor.synthesize_multi_answer({**state, **plan, **execution})
        invoke.assert_not_awaited()
        self.assertTrue(final["routing_metadata"]["planning_error"])
        self.assertIn("could not resolve", final["messages"][0].content)

    async def test_dependency_execution_and_partial_failure_preserve_all_tasks_and_citations(self):
        tasks = [task("q1","hr"), task("q2","admin"), task("q3","it",depends_on=["q1"]),
                 task("q4","finance",depends_on=["q2"])]
        calls = []
        async def invoke(agent, state, query):
            calls.append(agent)
            if agent == "admin":
                return agent, ""
            if agent == "it":
                self.assertIn("Prior answers needed", query)
                self.assertIn("HR answer", query)
            return agent, f"A complete {agent.upper()} answer with the exact value 12.\n\nSources: [{agent}](https://example.test/{agent})"
        state = {**self.state, **route("multi_delegate", ["hr","admin","it","finance"]), "routing_tasks":tasks}
        with patch.object(supervisor, "_invoke_specialist_for_fan_out", side_effect=invoke):
            executed = await supervisor.multi_delegate(state)
        self.assertEqual(executed["routing_task_status"], {"q1":"answered","q2":"unavailable","q3":"answered","q4":"dependency_failed"})
        self.assertNotIn("finance", calls)
        final = await supervisor.synthesize_multi_answer({**state, **executed})
        text = final["messages"][0].content
        self.assertIn("HR answer", text)
        self.assertIn("IT answer", text)
        self.assertEqual(text.count("could not complete"), 2)
        self.assertEqual(text.count("Sources:"), 1)
        self.assertEqual(final["routing_metadata"]["planned_tasks"], 4)
        self.assertEqual(final["routing_metadata"]["answered_tasks"], 2)

    async def test_compiled_graph_routes_three_agents_and_saves_followup_context(self):
        tasks = [task("q1","hr"),task("q2","admin"),task("q3","it")]
        planner = SimpleNamespace(ainvoke=AsyncMock(return_value=supervisor._RoutingPlan(tasks=tasks, unassigned_questions=[])))
        async def invoke(agent, state, query):
            return agent, f"This is a complete grounded answer from {agent}, preserving 12 days."
        with patch.object(supervisor, "_build_delegate_node", return_value=lambda state: {}), \
             patch.object(supervisor, "classify_departments", AsyncMock(return_value=prediction(hr=.99,admin=.98,it=.97))), \
             patch.object(supervisor, "_get_routing_planner", return_value=planner), \
             patch.object(supervisor, "_invoke_specialist_for_fan_out", side_effect=invoke):
            graph = supervisor.build_supervisor_workflow().compile(checkpointer=MemorySaver())
            state = await graph.ainvoke(self.state, {"configurable":{"thread_id":"synthetic"}})
        self.assertEqual(state["last_specialist_agents"], ["hr","admin","it"])
        self.assertEqual(state["routing_metadata"]["answered_tasks"], 3)
        self.assertEqual(len(state["messages"]), 2)

    async def test_correlated_logs_show_routing_tasks_and_failures_without_private_text(self):
        state = {**self.state, "thread_id":"private-owner@example.test",
                 "messages":[HumanMessage(content="owner@example.test needs leave and transport help") ]}
        tasks = [task("q1","hr", "Email owner@example.test about leave"), task("q2","admin"),
                 task("q3","it",depends_on=["q2"])]
        planner = SimpleNamespace(ainvoke=AsyncMock(return_value=supervisor._RoutingPlan(tasks=tasks, unassigned_questions=[])))
        async def invoke(agent, state, query):
            return agent, "PRIVATE_API_RESPONSE with complete policy details" if agent == "hr" else ""
        with patch.object(supervisor.settings, "SUPERVISOR_ROUTING_LOG_TEXT", False), \
             patch.object(supervisor, "classify_departments", AsyncMock(return_value=prediction(hr=.99,admin=.98,it=.97))), \
             patch.object(supervisor, "_get_routing_planner", return_value=planner), \
             patch.object(supervisor, "_invoke_specialist_for_fan_out", side_effect=invoke), \
             self.assertLogs("domain.supervisor", level="INFO") as captured:
            state.update(await supervisor.route_request(state))
            state.update(await supervisor.decompose_query(state))
            state.update(await supervisor.multi_delegate(state))
            await supervisor.synthesize_multi_answer(state)
        events = [json.loads(record.getMessage().split("Supervisor trace | ",1)[1]) for record in captured.records]
        self.assertEqual(len({e["trace_id"] for e in events}),1)
        self.assertEqual(len(events[0]["trace_id"]),32)
        self.assertEqual(len({e["conversation_id"] for e in events}),1)
        outcome = next(e for e in events if e["event"] == "routing_decided")
        self.assertEqual(outcome["agents"],["hr","admin","it"])
        self.assertEqual(outcome["probabilities"]["hr"],.99)
        self.assertIn("total_latency_ms",outcome["metadata"])
        plan = next(e for e in events if e["event"] == "planning_completed")
        self.assertEqual(plan["tasks"][2]["depends_on"],["q2"])
        completed = {e["task_id"]:e["status"] for e in events if e["event"] == "task_completed"}
        self.assertEqual(completed,{"q1":"answered","q2":"unavailable","q3":"dependency_failed"})
        final = next(e for e in events if e["event"] == "synthesis_completed")
        self.assertEqual((final["planned_tasks"],final["answered_tasks"]),(3,1))
        for private in ("owner@example.test", "PRIVATE_API_RESPONSE", "question_preview"):
            self.assertNotIn(private,"\n".join(captured.output))

    def test_optional_query_previews_mask_identifiers_and_bound_log_size(self):
        with patch.object(supervisor.settings,"SUPERVISOR_ROUTING_LOG_TEXT",True), \
             self.assertLogs("domain.supervisor",level="INFO") as captured:
            emit_event({"routing_metadata":{"trace_id":"synthetic"}},"preview", 
                       **query_preview("owner@example.test 0771234567 " + "x" * 700))
        event=json.loads(captured.records[0].getMessage().split("Supervisor trace | ",1)[1])
        self.assertIn("[EMAIL]",event["question_preview"])
        self.assertIn("[PHONE]",event["question_preview"])
        self.assertLessEqual(len(event["question_preview"]),500)
        self.assertNotIn("owner@example.test",captured.output[0])


class EvaluationTests(unittest.TestCase):
    def test_extra_agents_and_missing_third_agent_reduce_scores(self):
        case = {"id":"three", "language":"en", "expected_action":"multi_delegate", "expected_agents":["hr","admin","it"]}
        old = {"case_id":"three", "backend":"legacy", "latency_ms":1,
               "result":route("multi_delegate",["hr","admin","finance"])}
        new = {"case_id":"three", "backend":"decisions", "latency_ms":2,
               "result":route("multi_delegate",["hr","admin","it"])}
        summary = compare([case],[old,new])
        self.assertEqual(summary["legacy"]["missed_agents"],1)
        self.assertEqual(summary["legacy"]["unnecessary_agents"],1)
        self.assertEqual(summary["paired"]["wins"],1)
        self.assertEqual(summary["decisions"]["exact_accuracy"],1)

    def test_provider_errors_are_wrong_and_unpaired_records_are_rejected(self):
        case = {"id":"one", "language":"en", "expected_action":"delegate", "expected_agents":["hr"]}
        row = {"case_id":"one", "backend":"decisions", "latency_ms":1, "error":"TimeoutError", "result":{}}
        self.assertFalse(score_case(case,row)["correct"])
        self.assertEqual(score_case(case,row)["fn"],1)
        with self.assertRaisesRegex(ValueError,"unpaired"):
            compare([case],[row])


if __name__ == "__main__":
    unittest.main()
