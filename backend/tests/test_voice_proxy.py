"""Offline regression tests for the actual Gemini proxy coroutine (no credentials needed)."""
import ast
import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch


def load_proxy():
    # Load the actual coroutine without importing the application's settings or
    # optional provider packages. Its network boundaries are replaced below.
    path = Path(__file__).parents[1] / 'routers' / 'voice_agent' / 'realtime.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    fn = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'gemini_voice_proxy')
    fn.decorator_list = []
    namespace = dict(asyncio=asyncio, json=json, logger=logging.getLogger('voice-test'),
                     WebSocket=object, WebSocketDisconnect=ConnectionError,
                     settings=SimpleNamespace(PROJECT_ID='test', OPENAI_API_KEY=''),
                     _get_vertex_access_token=lambda: 'test-token',
                     VOICE_SYSTEM_PROMPT='Hello {USER_FIRST_NAME}', WORKMATE_TOOL={},
                     GEMINI_LIVE_MODEL='test', MAX_AUDIO_LEAD_SECONDS=0.5,
                     _normalize_tool_question=lambda value: ' '.join(value.strip().casefold().split()).rstrip('.?!'),
                     _estimate_chunk_seconds=lambda data: 0.01)
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace


class Socket:
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.sent = []

    async def accept(self):
        pass

    async def receive_text(self):
        item = await self.incoming.get()
        if item is None:
            raise ConnectionError('disconnected')
        return json.dumps(item)

    async def send_text(self, value):
        self.sent.append(json.loads(value))

    send = send_text

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self.incoming.get()
        if item is None:
            raise StopAsyncIteration
        return json.dumps(item)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def call(call_id, question='What is my leave balance?'):
    return {'toolCall': {'functionCalls': [{'id': call_id, 'name': 'ask_workmate_ai',
                                           'args': {'question': question}}]}}


class VoiceProxyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.browser, self.gemini = Socket(), Socket()
        self.ns = load_proxy()
        self.agent = AsyncMock(return_value='Your leave balance is 14 days.')
        self.ns['_ask_agent'] = self.agent
        self.patch = patch.dict('sys.modules', {'websockets': SimpleNamespace(connect=lambda *a, **k: self.gemini)})
        self.patch.start()
        await self.browser.incoming.put({'type': 'user_identity', 'user_id': 'employee', 'user_name': 'Test User'})
        self.session = asyncio.create_task(self.ns['gemini_voice_proxy'](self.browser))
        await self.until(lambda: bool(self.gemini.sent))

    async def asyncTearDown(self):
        await self.browser.incoming.put({'type': 'end'})
        await asyncio.wait_for(self.session, 1)
        self.patch.stop()

    async def until(self, predicate):
        async with asyncio.timeout(1):
            while not predicate():
                await asyncio.sleep(0.001)

    def results(self):
        return [
            response
            for message in self.gemini.sent
            if 'tool_response' in message
            for response in message['tool_response']['function_responses']
        ]

    async def test_slow_lookup_duplicate_and_correlated_complete_answer(self):
        gate = asyncio.Event()
        async def slow(**kwargs):
            await gate.wait()
            return 'Your leave balance is 14 days.'
        self.agent.side_effect = slow
        await self.gemini.incoming.put(call('one'))
        await self.until(lambda: self.agent.await_count == 1)
        await self.gemini.incoming.put(call('one'))
        await self.gemini.incoming.put({'serverContent': {'turnComplete': True}})
        await self.until(lambda: any(m.get('type') == 'turn_complete' for m in self.browser.sent))
        self.assertEqual(self.agent.await_count, 1)
        self.assertFalse(any('client_content' in m for m in self.gemini.sent))
        self.assertFalse(any(m.get('type') == 'stop_audio' for m in self.browser.sent))
        gate.set()
        await self.until(lambda: len(self.results()) == 1)
        self.assertEqual(self.results()[0], {'id': 'one', 'name': 'ask_workmate_ai',
                                          'response': {'output': 'Your leave balance is 14 days.'}})

    async def test_empty_question_never_reaches_chat(self):
        await self.gemini.incoming.put(call('empty', '  '))
        await self.until(lambda: len(self.results()) == 1)
        self.agent.assert_not_awaited()
        self.assertIn('error', self.results()[0]['response'])

    async def test_new_call_does_not_cancel_pending_lookup(self):
        gate = asyncio.Event()
        async def slow(**kwargs):
            await gate.wait()
            return 'Complete answer'
        self.agent.side_effect = slow
        await self.gemini.incoming.put(call('first'))
        await self.until(lambda: self.agent.await_count == 1)
        await self.gemini.incoming.put(call('second', 'How do I apply for leave?'))
        await self.gemini.incoming.put({'serverContent': {'turnComplete': True}})
        await self.until(lambda: any(m.get('type') == 'turn_complete' for m in self.browser.sent))
        self.assertEqual(self.agent.await_count, 1)
        gate.set()
        await self.until(lambda: len(self.results()) == 2)
        self.assertEqual([r['id'] for r in self.results()], ['first', 'second'])
        self.assertTrue(all(r['response']['output'] == 'Complete answer' for r in self.results()))
        self.assertFalse(any(m.get('type') == 'stop_audio' for m in self.browser.sent))

    async def test_same_question_with_new_call_id_is_coalesced(self):
        gate = asyncio.Event()
        async def slow(**kwargs):
            await gate.wait()
            return 'Your leave balance is 14 days.'
        self.agent.side_effect = slow
        await self.gemini.incoming.put(call('first', 'What is my leave balance?'))
        await self.until(lambda: self.agent.await_count == 1)
        await self.gemini.incoming.put(call('second', '  what is my leave balance  '))
        await asyncio.sleep(0.01)
        self.assertEqual(self.agent.await_count, 1)
        gate.set()
        await self.until(lambda: len(self.results()) == 2)
        self.assertEqual([result['id'] for result in self.results()], ['first', 'second'])
        self.assertEqual(self.agent.await_count, 1)

    async def test_explicit_cancellation_stops_audio_without_empty_response(self):
        cancelled = asyncio.Event()
        async def slow(**kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        self.agent.side_effect = slow
        await self.gemini.incoming.put(call('cancel-me'))
        await self.until(lambda: self.agent.await_count == 1)
        await self.gemini.incoming.put({'toolCallCancellation': {'ids': ['cancel-me']},
                                      'serverContent': {'interrupted': True}})
        await asyncio.wait_for(cancelled.wait(), 1)
        self.assertTrue(any(m.get('type') == 'stop_audio' for m in self.browser.sent))
        self.assertEqual(self.results(), [])

    async def test_end_cancels_pending_lookup(self):
        cancelled = asyncio.Event()
        async def slow(**kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        self.agent.side_effect = slow
        await self.gemini.incoming.put(call('pending'))
        await self.until(lambda: self.agent.await_count == 1)
        await self.browser.incoming.put({'type': 'end'})
        await asyncio.wait_for(self.session, 1)
        self.assertTrue(cancelled.is_set())
        self.assertEqual(self.results(), [])

    async def test_audio_waits_for_setup_ack(self):
        await self.browser.incoming.put({'type': 'audio', 'data': 'AAAA'})
        await self.gemini.incoming.put({'serverContent': {'turnComplete': True}})
        await self.until(lambda: any(m.get('type') == 'turn_complete' for m in self.browser.sent))
        self.assertFalse(any('realtime_input' in m for m in self.gemini.sent))
        await self.gemini.incoming.put({'setupComplete': {}})
        await self.until(lambda: any('realtime_input' in m for m in self.gemini.sent))

    async def test_provider_disconnect_ends_session(self):
        await self.gemini.incoming.put(None)
        await asyncio.wait_for(self.session, 1)
        self.assertEqual(self.browser.sent[-1]['type'], 'session_end')


if __name__ == '__main__':
    unittest.main()
