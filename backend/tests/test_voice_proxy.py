"""Offline regression tests for the actual Gemini proxy coroutine (no credentials needed)."""
import ast
import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from backend.services.voice_fillers import FillerScheduler
from backend.services.voice_language_id import AudioLanguageWindow, identify_audio


def load_proxy():
    # Load the actual coroutine without importing the application's settings or
    # optional provider packages. Its network boundaries are replaced below.
    path = Path(__file__).parents[1] / 'routers' / 'voice_agent' / 'realtime.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    selected = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name in {
                    '_is_resource_exhausted', '_connect_gemini_with_backoff', '_voice_error_payload',
                    '_claim_voice_session', '_release_voice_session', 'resolve_voice_language',
                    '_native_script_language', '_parse_voice_language_preference', '_language_instruction', '_voice_diagnostic', 'gemini_voice_proxy',
                }]
    fn = next(n for n in selected if n.name == 'gemini_voice_proxy')
    fn.decorator_list = []
    namespace = dict(asyncio=asyncio, json=json, logger=logging.getLogger('voice-test'),
                     FillerScheduler=FillerScheduler, AudioLanguageWindow=AudioLanguageWindow,
                     identify_audio=identify_audio,
                     warm_audio_language_model=AsyncMock(return_value=True),
                     WebSocket=object, WebSocketDisconnect=ConnectionError,
                     settings=SimpleNamespace(PROJECT_ID='test', LOCATION='us-central1', OPENAI_API_KEY='', VOICE_DIAGNOSTICS_ENABLED=False),
                     HTTPException=HTTPException, HTTPAuthorizationCredentials=HTTPAuthorizationCredentials,
                     get_verified_user=AsyncMock(return_value={'email': 'employee'}),
                     chat_identity=lambda user, guest: SimpleNamespace(user_id='employee', user_name='Test User'),
                     uuid4=uuid4,
                     _get_vertex_access_token=lambda: 'test-token',
                     VOICE_SYSTEM_PROMPT='Hello {USER_FIRST_NAME}', WORKMATE_TOOL={},
                     GEMINI_LIVE_MODEL='test', MAX_AUDIO_LEAD_SECONDS=0.5,
                     GEMINI_CONNECT_ATTEMPTS=3, GEMINI_CONNECT_BACKOFF_SECONDS=0,
                     _ACTIVE_VOICE_SESSIONS={},
                     _normalize_tool_question=lambda value: ' '.join(value.strip().casefold().split()).rstrip('.?!'),
                     _estimate_chunk_seconds=lambda data: 0.01,
                     VOICE_LANGUAGE_EN='en-US', VOICE_LANGUAGE_SI='si-LK', VOICE_LANGUAGE_TA='ta-LK',
                     VOICE_LANGUAGE_PREFERENCES=frozenset({'en-US', 'si-LK', 'ta-LK'}),
                     ROMANIZED_SINHALA_WORDS=frozenset({
                         'hari', 'kohomada', 'kohomadha', 'oyaata', 'oyata', 'mata', 'karanna',
                         'puluwanda', 'istuti', 'ayubowan', 'mokakda', 'mokadda',
                     }),
                     ROMANIZED_TAMIL_WORDS=frozenset({
                         'sari', 'vanakkam', 'enna', 'epadi', 'eppadi', 'sollunga', 'sollu',
                         'nandri', 'venum', 'vendum', 'seyyalam', 'seiyalam', 'irukku',
                         'ennudaya', 'ennoda', 'evvalavu', 'evalavu', 'enakku', 'solunga', 'irukka',
                     }), re=__import__('re'), random=__import__('random'))
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), 'exec'), namespace)
    return namespace


class Socket:
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.sent = []

    async def accept(self):
        pass

    async def close(self, code=1000):
        self.close_code = code

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


def input_transcript(text):
    return {'serverContent': {'inputTranscription': {'text': text}}}


class VoiceProxyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.browser, self.gemini = Socket(), Socket()
        self.ns = load_proxy()
        self.agent = AsyncMock(return_value='Your leave balance is 14 days.')
        self.ns['_ask_agent'] = self.agent
        async def connect(*_args, **_kwargs):
            return self.gemini
        self.patch = patch.dict('sys.modules', {'websockets': SimpleNamespace(connect=connect)})
        self.patch.start()
        await self.browser.incoming.put({'type': 'user_identity', 'user_id': 'employee',
                                         'user_name': 'Test User', 'auth_token': 'test-auth-token'})
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

    def filler_prompts(self):
        return [message['client_content']['turns'][0]['parts'][0]['text']
                for message in self.gemini.sent if 'client_content' in message
                and '[LUMA_FILLER_ACK]' in message['client_content']['turns'][0]['parts'][0]['text']]

    async def test_slow_workmate_lookup_emits_spoken_filler_event(self):
        gate = asyncio.Event()
        async def slow(**kwargs):
            await gate.wait()
            return 'Grounded answer'
        async def identified(_pcm):
            return {'language': 'en', 'confidence': .99, 'method': 'audio_lid'}
        self.agent.side_effect = slow
        self.ns['identify_audio'] = identified
        await self.gemini.incoming.put(input_transcript('What is my leave balance?'))
        await self.gemini.incoming.put(call('slow'))
        async with asyncio.timeout(4):
            while not self.filler_prompts():
                await asyncio.sleep(.02)
        self.assertIn('English', self.filler_prompts()[0])
        await self.gemini.incoming.put(call('duplicate-during-filler', 'Let me check that for you.'))
        await asyncio.sleep(.02)
        self.assertEqual(self.agent.await_count, 1)
        await self.gemini.incoming.put({'serverContent': {
            'modelTurn': {'parts': [{'inlineData': {'data': 'AAAA'}}]},
            'outputTranscription': {'text': 'Let me check that for you.'},
        }})
        await self.until(lambda: any(m.get('type') == 'filler_audio' for m in self.browser.sent))
        self.assertFalse(any(m.get('type') == 'filler_audio_complete' for m in self.browser.sent))
        await self.gemini.incoming.put({'serverContent': {'turnComplete': True}})
        await self.until(lambda: any(m.get('type') == 'filler_audio_complete' for m in self.browser.sent))
        filler = next(m for m in self.browser.sent if m.get('type') == 'filler_audio')
        self.assertEqual(filler['language'], 'en')
        self.assertEqual(filler['turn_id'], 'voice_turn_1_filler_1')
        gate.set()
        await self.until(lambda: len(self.results()) == 2)

    async def test_native_script_turn_can_start_filler_when_audio_detector_is_inconclusive(self):
        gate = asyncio.Event()
        async def slow(**kwargs):
            await gate.wait()
            return 'Grounded answer'
        async def inconclusive(_pcm):
            return {'language': None, 'confidence': None, 'method': 'insufficient_speech'}
        self.agent.side_effect = slow
        self.ns['identify_audio'] = inconclusive
        await self.gemini.incoming.put(call('slow-si', 'මගේ නිවාඩු දින කීයද?'))
        async with asyncio.timeout(4):
            while not self.filler_prompts():
                await asyncio.sleep(.02)
        self.assertIn('Sinhala', self.filler_prompts()[0])
        await self.gemini.incoming.put({'serverContent': {
            'modelTurn': {'parts': [{'inlineData': {'data': 'AAAA'}}]},
            'outputTranscription': {'text': '\u0dc4\u0dbb\u0dd2, \u0db8\u0db8 \u0d91\u0d9a \u0db6\u0dbd\u0db1\u0dca\u0db1\u0db8\u0dca.'},
            'turnComplete': True,
        }})
        await self.until(lambda: any(m.get('type') == 'filler_audio' for m in self.browser.sent))
        filler = next(m for m in self.browser.sent if m.get('type') == 'filler_audio')
        self.assertEqual(filler['language'], 'si')
        gate.set()
        await self.until(lambda: len(self.results()) == 1)

    async def test_english_turn_can_start_filler_when_audio_detector_is_inconclusive(self):
        gate = asyncio.Event()
        async def slow(**kwargs):
            await gate.wait()
            return 'Grounded answer'
        async def inconclusive(_pcm):
            return {'language': None, 'confidence': None, 'method': 'insufficient_speech'}
        self.agent.side_effect = slow
        self.ns['identify_audio'] = inconclusive
        await self.gemini.incoming.put(input_transcript('What is my leave balance?'))
        await self.gemini.incoming.put(call('slow-en'))
        async with asyncio.timeout(4):
            while not self.filler_prompts():
                await asyncio.sleep(.02)
        self.assertIn('English', self.filler_prompts()[0])
        await self.gemini.incoming.put({'serverContent': {
            'modelTurn': {'parts': [{'inlineData': {'data': 'AAAA'}}]},
            'outputTranscription': {'text': 'Let me check that for you.'},
            'turnComplete': True,
        }})
        await self.until(lambda: any(m.get('type') == 'filler_audio' for m in self.browser.sent))
        filler = next(m for m in self.browser.sent if m.get('type') == 'filler_audio')
        self.assertEqual(filler['language'], 'en')
        gate.set()
        await self.until(lambda: len(self.results()) == 1)

    async def test_answer_ready_discards_late_filler_audio_before_tool_response(self):
        gate = asyncio.Event()
        async def slow(**kwargs):
            await gate.wait()
            return 'Grounded answer'
        self.agent.side_effect = slow
        self.ns['identify_audio'] = AsyncMock(return_value={
            'language': 'ta', 'confidence': .96, 'method': 'audio_lid',
        })
        await self.gemini.incoming.put(input_transcript('What is my leave balance?'))
        await self.gemini.incoming.put(call('late-filler'))
        async with asyncio.timeout(4):
            while not self.filler_prompts():
                await asyncio.sleep(.02)
        gate.set()
        await self.until(lambda: any(m.get('type') == 'stop_filler_audio' for m in self.browser.sent))
        await self.gemini.incoming.put({'serverContent': {
            'modelTurn': {'parts': [{'inlineData': {'data': 'AAAA'}}]},
            'outputTranscription': {'text': '\u0b9a\u0bb0\u0bbf'},
            'turnComplete': True,
        }})
        await self.until(lambda: len(self.results()) == 1)
        self.assertFalse(any(m.get('type') == 'filler_audio' for m in self.browser.sent))
        self.assertEqual(self.results()[0]['response']['response_language'], 'ta-LK')

    async def test_slow_audio_detector_does_not_block_first_filler(self):
        gate = asyncio.Event()
        async def slow_lookup(**kwargs):
            await gate.wait()
            return 'Grounded answer'
        async def slow_detector(_pcm):
            await asyncio.sleep(5)
            return {'language': 'en', 'confidence': .99, 'method': 'audio_lid'}
        self.agent.side_effect = slow_lookup
        self.ns['identify_audio'] = slow_detector
        await self.gemini.incoming.put(input_transcript('What is my leave balance?'))
        await self.gemini.incoming.put(call('slow-detector'))
        async with asyncio.timeout(3):
            while not self.filler_prompts():
                await asyncio.sleep(.02)
        self.assertIn('English', self.filler_prompts()[0])
        gate.set()
        await self.gemini.incoming.put({'serverContent': {
            'modelTurn': {'parts': [{'inlineData': {'data': 'AAAA'}}]},
            'turnComplete': True,
        }})
        await self.until(lambda: len(self.results()) == 1)

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
        self.assertFalse(any(m.get('type') == 'stop_audio' for m in self.browser.sent))
        gate.set()
        await self.until(lambda: len(self.results()) == 1)
        delivery_index = next(
            index for index, message in enumerate(self.gemini.sent)
            if 'client_content' in message
        )
        tool_response_index = next(
            index for index, message in enumerate(self.gemini.sent)
            if 'tool_response' in message
        )
        self.assertLess(delivery_index, tool_response_index)
        response = self.results()[0]
        self.assertEqual(response['id'], 'one')
        self.assertEqual(response['name'], 'ask_workmate_ai')
        self.assertEqual(response['response']['output'], 'Your leave balance is 14 days.')
        self.assertEqual(response['response']['response_language'], 'en-US')

    async def test_empty_question_never_reaches_chat(self):
        await self.gemini.incoming.put(call('empty', '  '))
        await self.until(lambda: len(self.results()) == 1)
        self.agent.assert_not_awaited()
        self.assertIn('error', self.results()[0]['response'])

    async def test_verified_identity_and_token_are_forwarded_to_chat(self):
        await self.gemini.incoming.put(call('identity'))
        await self.until(lambda: len(self.results()) == 1)
        self.assertEqual(self.agent.call_args.kwargs['auth_token'], 'test-auth-token')
        self.assertEqual(self.agent.call_args.kwargs['user_id'], 'employee')
        self.assertEqual(self.agent.call_args.kwargs['user_name'], 'Test User')
        self.ns['get_verified_user'].assert_awaited_once()

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

    async def test_second_session_for_same_account_is_rejected_before_provider_connect(self):
        second_browser = Socket()
        await second_browser.incoming.put({
            'type': 'user_identity', 'user_id': 'employee', 'user_name': 'Test User',
            'auth_token': 'test-auth-token',
        })
        second_session = asyncio.create_task(self.ns['gemini_voice_proxy'](second_browser))
        await self.until(lambda: any(message.get('code') == 'VOICE_SESSION_ACTIVE' for message in second_browser.sent))
        await asyncio.wait_for(second_session, 1)
        error = next(message for message in second_browser.sent if message.get('code') == 'VOICE_SESSION_ACTIVE')
        self.assertNotIn('Gemini', error['message'])

    def test_language_resolution_is_per_turn_and_unicode_safe(self):
        resolve = self.ns['resolve_voice_language']
        self.assertEqual(resolve('Hello Luma'), 'en-US')
        self.assertEqual(resolve('\u0d94\u0dba\u0dcf\u0da7 \u0d9a\u0ddc\u0dc4\u0ddc\u0db8\u0daf?'), 'si-LK')
        self.assertEqual(resolve('\u0b87\u0baa\u0bcd\u0baa\u0bcb\u0ba4\u0bc1 \u0b8e\u0ba9\u0bcd\u0ba9 \u0b9a\u0bc6\u0baf\u0bcd\u0baf\u0bb2\u0bbe\u0bae\u0bcd?'), 'ta-LK')
        self.assertEqual(resolve('Hello, \u0d9a\u0ddc\u0dc4\u0ddc\u0db8\u0daf?'), 'si-LK')
        self.assertEqual(resolve('Okay, \u0b9a\u0bca\u0bb2\u0bcd\u0bb2\u0bc1\u0b99\u0bcd\u0b95\u0bb3\u0bcd.'), 'ta-LK')
        self.assertEqual(resolve('hari'), 'si-LK')
        self.assertEqual(resolve('sari'), 'ta-LK')
        self.assertEqual(resolve('Ennudaya leave balance evvalavu?'), 'ta-LK')
        self.assertEqual(resolve('Workmate AI'), 'en-US')

    def test_explicit_language_preference_is_validated(self):
        parse = self.ns['_parse_voice_language_preference']
        self.assertEqual(parse('ta-LK'), 'ta-LK')
        self.assertEqual(parse('si-LK'), 'si-LK')
        self.assertIsNone(parse('Tamil'))
        self.assertIsNone(parse(None))

    async def test_tamil_language_preference_overrides_an_english_transcription(self):
        await self.browser.incoming.put({'type': 'language_preference', 'language': 'ta-LK'})
        await self.until(lambda: any('client_content' in message for message in self.gemini.sent))
        await self.gemini.incoming.put(input_transcript('What is my leave balance?'))
        await self.gemini.incoming.put(call('tamil-preference', 'What is my leave balance?'))
        await self.until(lambda: len(self.results()) == 1)
        self.assertEqual(self.results()[0]['response']['response_language'], 'ta-LK')

    async def test_audio_tamil_controls_filler_and_tool_response_when_asr_is_english(self):
        gate = asyncio.Event()
        async def slow(**_kwargs):
            await gate.wait()
            return 'Grounded leave answer'
        self.agent.side_effect = slow
        self.ns['identify_audio'] = AsyncMock(return_value={
            'language': 'ta', 'confidence': .96, 'method': 'audio_lid',
        })
        await self.gemini.incoming.put(input_transcript('What is my leave balance?'))
        await self.gemini.incoming.put(call('audio-tamil', 'What is my leave balance?'))
        await self.until(lambda: any(message.get('type') == 'filler_task_started'
                                     for message in self.browser.sent))
        async with asyncio.timeout(4):
            while not self.filler_prompts():
                await asyncio.sleep(.02)
        self.assertIn('Tamil', self.filler_prompts()[0])
        await self.gemini.incoming.put({'serverContent': {
            'modelTurn': {'parts': [{'inlineData': {'data': 'AAAA'}}]},
            'outputTranscription': {'text': '\u0b9a\u0bb0\u0bbf, \u0ba8\u0bbe\u0ba9\u0bcd \u0b85\u0ba4\u0bc8 \u0baa\u0bbe\u0bb0\u0bcd\u0ba4\u0bcd\u0ba4\u0bc1 \u0b9a\u0bca\u0bb2\u0bcd\u0b95\u0bbf\u0bb1\u0bc7\u0ba9\u0bcd.'},
            'turnComplete': True,
        }})
        await self.until(lambda: any(m.get('type') == 'filler_audio' for m in self.browser.sent))
        self.assertEqual(next(m for m in self.browser.sent if m.get('type') == 'filler_audio')['language'], 'ta')
        gate.set()
        await self.until(lambda: len(self.results()) == 1)
        started = next(message for message in self.browser.sent
                       if message.get('type') == 'filler_task_started')
        self.assertEqual(started['language'], 'ta')
        self.assertEqual(self.results()[0]['response']['response_language'], 'ta-LK')
        self.assertEqual(self.results()[0]['scheduling'], 'INTERRUPT')
        self.assertTrue(any(message.get('type') == 'filler_task_stopped'
                            and message.get('reason') == 'answer_ready'
                            for message in self.browser.sent))

    async def test_romanized_tamil_turn_requests_tamil_filler(self):
        gate = asyncio.Event()
        async def slow(**_kwargs):
            await gate.wait()
            return 'Grounded leave answer'
        self.agent.side_effect = slow
        await self.gemini.incoming.put(input_transcript('Ennudaya leave balance evvalavu?'))
        await self.gemini.incoming.put(call('romanized-tamil', 'What is my leave balance?'))
        async with asyncio.timeout(3):
            while not self.filler_prompts():
                await asyncio.sleep(.02)
        self.assertIn('Tamil', self.filler_prompts()[0])
        await self.gemini.incoming.put({'serverContent': {
            'modelTurn': {'parts': [{'inlineData': {'data': 'AAAA'}}]},
            'outputTranscription': {'text': '\u0b9a\u0bb0\u0bbf, \u0ba8\u0bbe\u0ba9\u0bcd \u0baa\u0bbe\u0bb0\u0bcd\u0ba4\u0bcd\u0ba4\u0bc1\u0b9a\u0bcd \u0b9a\u0bca\u0bb2\u0bcd\u0b95\u0bbf\u0bb1\u0bc7\u0ba9\u0bcd.'},
            'turnComplete': True,
        }})
        await self.until(lambda: any(m.get('type') == 'filler_audio' for m in self.browser.sent))
        gate.set()
        await self.until(lambda: len(self.results()) == 1)
        self.assertEqual(self.results()[0]['response']['response_language'], 'ta-LK')

    def test_live_setup_includes_multilingual_asr_hints(self):
        setup = next(message['setup'] for message in self.gemini.sent if 'setup' in message)
        transcription = setup['input_audio_transcription']
        self.assertEqual(transcription['language_codes'], ['en-US', 'si-LK', 'ta-IN'])
        self.assertEqual(transcription['mode'], 'VERBATIM')

    def test_workmate_tool_is_non_blocking_for_same_session_fillers(self):
        path = Path(__file__).parents[1] / 'routers' / 'voice_agent' / 'realtime.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        assignment = next(node for node in tree.body if isinstance(node, ast.Assign)
                          and any(isinstance(target, ast.Name) and target.id == 'WORKMATE_TOOL'
                                  for target in node.targets))
        self.assertEqual(ast.literal_eval(assignment.value)['behavior'], 'NON_BLOCKING')

    async def test_tool_result_carries_the_current_turn_language(self):
        await self.gemini.incoming.put(call('tamil', '\u0b9a\u0bb0\u0bbf'))
        await self.until(lambda: len(self.results()) == 1)
        result = self.results()[0]['response']
        self.assertEqual(result['response_language'], 'ta-LK')
        self.assertIn('Tamil', result['instruction'])
        self.assertIn('RESPOND IN TAMIL. YOU MUST RESPOND UNMISTAKABLY IN TAMIL.', result['instruction'])
        self.assertIn('NATIVE AUDIO TARGET: Tamil (ta-IN).', result['instruction'])
        self.assertIn('தமிழ் script', result['instruction'])

    async def test_sinhala_tool_result_uses_an_explicit_provider_language_directive(self):
        await self.gemini.incoming.put(call('sinhala-language', '\u0db8\u0d9c\u0dda \u0db1\u0dd2\u0dc0\u0dcf\u0da9\u0dd4 \u0daf\u0dd2\u0db1 \u0d9a\u0dd3\u0dba\u0daf?'))
        await self.until(lambda: len(self.results()) == 1)
        result = self.results()[0]['response']
        self.assertEqual(result['response_language'], 'si-LK')
        self.assertIn('RESPOND IN SINHALA. YOU MUST RESPOND UNMISTAKABLY IN SINHALA.', result['instruction'])
        self.assertIn('Do not use English merely because the grounded tool result is English.', result['instruction'])

    async def test_tamil_language_policy_is_delivered_before_the_function_response(self):
        await self.gemini.incoming.put(call('tamil-delivery', '\u0b8e\u0ba9\u0bcd\u0ba9\u0bc1\u0b9f\u0bc8\u0baf \u0bb5\u0bbf\u0b9f\u0bc1\u0baa\u0bcd\u0baa\u0bc1 \u0b8e\u0bb5\u0bcd\u0bb5\u0bb3\u0bb5\u0bc1?'))
        await self.until(lambda: len(self.results()) == 1)
        delivery_index = next(index for index, message in enumerate(self.gemini.sent) if 'client_content' in message)
        response_index = next(index for index, message in enumerate(self.gemini.sent) if 'tool_response' in message)
        delivery = self.gemini.sent[delivery_index]['client_content']
        self.assertLess(delivery_index, response_index)
        self.assertFalse(delivery['turn_complete'])
        self.assertIn('RESPOND IN TAMIL. YOU MUST RESPOND UNMISTAKABLY IN TAMIL.', delivery['turns'][0]['parts'][0]['text'])

    async def test_tamil_tool_turn_binds_tamil_transcript_and_audio(self):
        await self.gemini.incoming.put(call('tamil-output', '\u0b8e\u0ba9\u0bcd\u0ba9\u0bc1\u0b9f\u0bc8\u0baf \u0bb5\u0bbf\u0b9f\u0bc1\u0baa\u0bcd\u0baa\u0bc1 \u0b8e\u0bb5\u0bcd\u0bb5\u0bb3\u0bb5\u0bc1?'))
        await self.until(lambda: len(self.results()) == 1)
        await self.gemini.incoming.put({
            'serverContent': {
                'modelTurn': {'parts': [{'inlineData': {'data': 'AAAA', 'mimeType': 'audio/pcm;rate=24000'}}]},
                'outputTranscription': {'text': '\u0b89\u0b99\u0bcd\u0b95\u0bb3\u0bbf\u0ba9\u0bcd \u0bb5\u0bbf\u0b9f\u0bc1\u0baa\u0bcd\u0baa\u0bc1 \u0bae\u0bc0\u0ba4\u0bae\u0bcd \u0b87\u0ba4\u0bc1.'},
            }
        })
        await self.until(lambda: any(message.get('role') == 'model' for message in self.browser.sent))
        audio = next(message for message in self.browser.sent if message.get('type') == 'audio')
        transcript = next(message for message in self.browser.sent if message.get('role') == 'model')
        self.assertEqual(audio['requested_output_language'], 'ta-LK')
        self.assertEqual(transcript['requested_output_language'], 'ta-LK')
        self.assertEqual(transcript['gemini_output_transcription_language'], 'ta-LK')
        self.assertEqual(audio['turn_id'], transcript['turn_id'])

    async def test_direct_sinhala_output_is_bound_to_its_input_turn(self):
        # This exercises the provider-output relay, rather than only checking
        # the function-response instruction.  Output transcription is the
        # only reliable text signal for the language Gemini actually generated.
        await self.gemini.incoming.put(input_transcript('\u0d94\u0dba\u0dcf\u0da7 \u0d9a\u0ddc\u0dc4\u0ddc\u0db8\u0daf?'))
        await self.gemini.incoming.put({
            'serverContent': {
                'modelTurn': {'parts': [{'inlineData': {'data': 'AAAA'}}]},
                'outputTranscription': {'text': '\u0db8\u0db8 \u0dc4\u0ddc\u0db3\u0db3\u0daf\u0dd2\u0db1\u0dca \u0d89\u0db1\u0dca\u0db1\u0dc0\u0dcf.'},
            }
        })
        await self.until(lambda: any(message.get('role') == 'model' for message in self.browser.sent))
        transcript = next(message for message in self.browser.sent if message.get('role') == 'model')
        self.assertEqual(transcript['turn_id'], 'voice_turn_1')
        self.assertEqual(transcript['requested_output_language'], 'si-LK')
        self.assertEqual(transcript['gemini_output_transcription_language'], 'si-LK')

    async def test_direct_tamil_output_is_bound_to_its_input_turn(self):
        await self.gemini.incoming.put(input_transcript('\u0b8e\u0baa\u0bcd\u0baa\u0b9f\u0bbf \u0b87\u0bb0\u0bc1\u0b95\u0bcd\u0b95\u0bbf\u0bb1\u0bcd\u0bb1\u0bc0\u0b99\u0bcd\u0b95\u0bb3\u0bcd?'))
        await self.gemini.incoming.put({
            'serverContent': {
                'modelTurn': {'parts': [{'inlineData': {'data': 'AAAA'}}]},
                'outputTranscription': {'text': '\u0ba8\u0bbe\u0ba9\u0bcd \u0ba8\u0ba9\u0bcd\u0bb1\u0bbe\u0b95 \u0b87\u0bb0\u0bc1\u0b95\u0bcd\u0b95\u0bbf\u0bb1\u0bcd\u0bb1\u0bc7\u0ba9\u0bcd.'},
            }
        })
        await self.until(lambda: any(message.get('role') == 'model' for message in self.browser.sent))
        transcript = next(message for message in self.browser.sent if message.get('role') == 'model')
        self.assertEqual(transcript['turn_id'], 'voice_turn_1')
        self.assertEqual(transcript['requested_output_language'], 'ta-LK')
        self.assertEqual(transcript['gemini_output_transcription_language'], 'ta-LK')

    async def test_direct_language_switches_keep_their_own_output_turns(self):
        turns = [
            ('Hello', 'Hello.', 'en-US'),
            ('\u0d94\u0dba\u0dcf\u0da7 \u0d9a\u0ddc\u0dc4\u0ddc\u0db8\u0daf?', '\u0db8\u0db8 \u0dc4\u0ddc\u0db3\u0db3\u0daf\u0dd2\u0db1\u0dca \u0d89\u0db1\u0dca\u0db1\u0dc0\u0dcf.', 'si-LK'),
            ('\u0b8e\u0baa\u0bcd\u0baa\u0b9f\u0bbf \u0b87\u0bb0\u0bc1\u0b95\u0bcd\u0b95\u0bbf\u0bb1\u0bcd\u0bb1\u0bc0\u0b99\u0bcd\u0b95\u0bb3\u0bcd?', '\u0ba8\u0bbe\u0ba9\u0bcd \u0ba8\u0ba9\u0bcd\u0bb1\u0bbe\u0b95 \u0b87\u0bb0\u0bc1\u0b95\u0bcd\u0b95\u0bbf\u0bb1\u0bcd\u0bb1\u0bc7\u0ba9\u0bcd.', 'ta-LK'),
        ]
        for index, (input_text, output_text, language) in enumerate(turns, start=1):
            await self.gemini.incoming.put(input_transcript(input_text))
            await self.gemini.incoming.put({
                'serverContent': {'outputTranscription': {'text': output_text}, 'turnComplete': True}
            })
            await self.until(lambda: len([m for m in self.browser.sent if m.get('role') == 'model']) == index)
            transcript = [m for m in self.browser.sent if m.get('role') == 'model'][-1]
            self.assertEqual(transcript['turn_id'], f'voice_turn_{index}')
            self.assertEqual(transcript['requested_output_language'], language)
            self.assertEqual(transcript['gemini_output_transcription_language'], language)

    async def test_english_tool_content_does_not_mask_actual_output_transcription_language(self):
        await self.gemini.incoming.put(call('sinhala-output', '\u0db8\u0d9c\u0dda \u0db1\u0dd2\u0dc0\u0dcf\u0da9\u0dd4 \u0daf\u0dd2\u0db1 \u0d9a\u0dd3\u0dba\u0daf?'))
        await self.until(lambda: len(self.results()) == 1)
        await self.gemini.incoming.put({
            'serverContent': {
                'modelTurn': {'parts': [{'inlineData': {'data': 'AAAA'}}]},
                # Deliberately English: the relay must report this as an
                # observed provider transcription, not relabel it Sinhala.
                'outputTranscription': {'text': 'Your leave balance is 14 days.'},
            }
        })
        await self.until(lambda: any(message.get('role') == 'model' for message in self.browser.sent))
        transcript = next(message for message in self.browser.sent if message.get('role') == 'model')
        self.assertEqual(transcript['requested_output_language'], 'si-LK')
        self.assertEqual(transcript['gemini_output_transcription_language'], 'en-US')

    async def test_romanized_sinhala_input_survives_english_tool_normalization(self):
        await self.gemini.incoming.put(input_transcript(
            'Luma oyata mage leave balance eka kiyanna puluwanda?'
        ))
        await self.gemini.incoming.put(call('leave', 'What is my leave balance?'))
        await self.until(lambda: len(self.results()) == 1)
        result = self.results()[0]['response']
        self.assertEqual(result['response_language'], 'si-LK')
        self.assertIn('Sinhala', result['instruction'])
        self.assertEqual(
            self.agent.call_args.kwargs['question'],
            'Luma oyata mage leave balance eka kiyanna puluwanda?',
        )

    async def test_tool_responses_keep_their_own_turn_languages(self):
        gate = asyncio.Event()
        invocation = 0

        async def delayed_first_question(**kwargs):
            nonlocal invocation
            invocation += 1
            if invocation == 1:
                await gate.wait()
            return 'Grounded answer'

        self.agent.side_effect = delayed_first_question
        await self.gemini.incoming.put(input_transcript('\u0d9a\u0ddc\u0dc4\u0ddc\u0db8\u0daf?'))
        await self.gemini.incoming.put(call('sinhala', 'How are you?'))
        await self.gemini.incoming.put(input_transcript('\u0b9a\u0bb0\u0bbf'))
        await self.gemini.incoming.put(call('tamil', 'Okay'))
        await self.until(lambda: self.agent.await_count >= 1)
        # The second turn has already been classified while the first Workmate
        # request is still pending. Releasing the old response must not make it
        # inherit the newer Tamil policy.
        gate.set()
        await self.until(lambda: len(self.results()) == 2)
        self.assertEqual(
            [result['response']['response_language'] for result in self.results()],
            ['si-LK', 'ta-LK'],
        )

    async def test_native_script_tool_argument_overrides_stale_english_transcript(self):
        # Reproduces production evidence where inputTranscription was English
        # although the Workmate function argument preserved Sinhala script.
        await self.gemini.incoming.put(input_transcript('What loan types does SLT offer?'))
        await self.gemini.incoming.put(call('loans', 'SLT \u0d91\u0d9a\u0dda \u0dad\u0dd2\u0dba\u0dd9\u0db1 \u0dab\u0dba \u0dc0\u0dbb\u0dca\u0d9c \u0db8\u0ddc\u0db1\u0dc0\u0daf?'))
        await self.until(lambda: len(self.results()) == 1)
        result = self.results()[0]['response']
        self.assertEqual(result['response_language'], 'si-LK')
        self.assertIn('Sinhala', result['instruction'])

    async def test_resource_exhausted_retries_only_new_connections(self):
        attempts = 0

        async def exhausted_connect(*_args, **_kwargs):
            nonlocal attempts
            attempts += 1
            raise RuntimeError('RESOURCE_EXHAUSTED')

        with self.assertRaises(RuntimeError):
            await self.ns['_connect_gemini_with_backoff'](
                SimpleNamespace(connect=exhausted_connect), 'wss://example.test'
            )
        self.assertEqual(attempts, 3)
        payload = self.ns['_voice_error_payload'](RuntimeError('Resource exhausted'))
        self.assertEqual(payload['code'], 'RESOURCE_EXHAUSTED')
        self.assertNotIn('RESOURCE_EXHAUSTED', payload['message'])


if __name__ == '__main__':
    unittest.main()
