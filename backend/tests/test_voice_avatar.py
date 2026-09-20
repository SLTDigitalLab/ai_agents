"""Isolated endpoint tests: no model, database, or paid provider calls.

Load the actual endpoint definitions without importing main's unrelated services.
"""
import ast
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

ROOT = Path(__file__).resolve().parents[1]


def load_function(path, name, namespace):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[name]


class VoiceChatCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # The graph emits the same events regardless of the HTTP response format.
        async def events(*args, **kwargs):
            for text in ("Policy ", "answer"):
                yield {"event": "on_chat_model_stream", "metadata": {"langgraph_node": "agent"},
                       "data": {"chunk": SimpleNamespace(content=text)}}
        self.graph = SimpleNamespace(astream_events=events, aupdate_state=AsyncMock())
        self.classify = AsyncMock(return_value=SimpleNamespace(action="ALLOW", sentiment="neutral", reason="ok"))
        self.namespace = dict(
            time=time, datetime=datetime, timezone=timezone,
            router=APIRouter(), ChatRequest=object, AsyncGenerator=AsyncGenerator,
            HTTPException=HTTPException, StreamingResponse=StreamingResponse,
            get_agent_builder=Mock(), clear_thread_evidence=Mock(),
            PII_MASK_EXEMPT_AGENTS={"lifestore", "enterprise"}, mask_pii=lambda text: text,
            CallbackHandler=Mock(), record_session=Mock(), classify_intent=self.classify,
            get_compiled_async_graph=AsyncMock(return_value=self.graph),
            logger=logging.getLogger("test"), _message_content_to_text=lambda content, **kw: content,
            _build_evidence_stream_chunk=lambda *args: "[[EVIDENCE_JSON]]test[[/EVIDENCE_JSON]]",
            BLOCK_MESSAGE="Blocked", BUSY_MESSAGE="Busy", GENERIC_ERROR_MESSAGE="Error",
            HumanMessage=lambda **kw: kw, AIMessage=lambda **kw: kw, _is_quota_error=lambda exc: False,
        )
        self.chat = load_function(ROOT / "routers/chat.py", "chat", self.namespace)

    def request(self, stream=True):
        return SimpleNamespace(agent_id="supervisor", message="Policy?", thread_id="voice-test",
            user_id="user", user_name="User", department=None, job_title=None, stream=stream)

    async def test_voice_returns_json_from_same_pipeline(self):
        self.assertEqual(await self.chat(self.request(False)), {"response": "Policy answer"})
        self.classify.assert_awaited_once_with("Policy?")
        self.namespace["record_session"].assert_called_once()

    async def test_chat_still_streams_text_and_evidence(self):
        response = await self.chat(self.request())
        self.assertIsInstance(response, StreamingResponse)
        chunks = [chunk async for chunk in response.body_iterator]
        self.assertEqual(chunks[:2], ["Policy ", "answer"])
        self.assertIn("[[EVIDENCE_JSON]]", chunks[-1])

    async def test_voice_still_obeys_guardrail_and_saves_block(self):
        self.classify.return_value.action = "BLOCK"
        self.assertEqual(await self.chat(self.request(False)), {"response": "Blocked"})
        self.graph.aupdate_state.assert_awaited_once()

    async def test_invalid_agent_still_returns_404(self):
        self.namespace["get_agent_builder"].side_effect = ValueError("Unknown agent")
        with self.assertRaises(HTTPException) as error:
            await self.chat(self.request(False))
        self.assertEqual(error.exception.status_code, 404)

    async def test_generation_failure_is_not_a_successful_voice_answer(self):
        self.namespace["get_compiled_async_graph"].side_effect = RuntimeError("private provider details")
        with self.assertRaises(HTTPException) as error:
            await self.chat(self.request(False))
        self.assertEqual(error.exception.status_code, 502)
        self.assertEqual(error.exception.detail, "Error")

    async def test_streaming_generation_failure_keeps_existing_behavior(self):
        self.namespace["get_compiled_async_graph"].side_effect = RuntimeError("private provider details")
        response = await self.chat(self.request())
        self.assertEqual([chunk async for chunk in response.body_iterator], ["Error"])


if __name__ == "__main__":
    unittest.main()
