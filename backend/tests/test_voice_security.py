"""Voice HTTP/WebSocket routes with signed Microsoft JWTs and mocked providers."""
import importlib.util
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import test_admin_security as fixtures
from core import auth


class VoiceSecurityTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.AdminSecurityTests.setUpClass.__func__)
    headers = fixtures.AdminSecurityTests.headers

    def setUp(self):
        for context in [
            patch.dict(os.environ, {'AZURE_AUTH_ENABLED': 'false'}),
            patch.object(auth, 'AZURE_CLIENT_ID', 'test-client'),
            patch.object(auth, 'AZURE_TENANT_ID', 'test-tenant'),
            patch.object(auth, 'ISSUER', 'https://login.microsoftonline.com/test-tenant/v2.0'),
            patch.object(auth, 'get_jwks', return_value={'keys': [self.public_jwk]}),
        ]:
            context.start()
            self.addCleanup(context.stop)
        self.settings = SimpleNamespace(OPENAI_API_KEY='synthetic', PROJECT_ID='test',
            LOCATION='us-central1', VOICE_CHAT_TIMEOUT_SECONDS=30)
        source = Path(__file__).parents[1] / 'routers' / 'voice_agent' / 'realtime.py'
        with patch.dict(sys.modules, {'core.config': fixtures.module('core.config', settings=self.settings)}):
            spec = importlib.util.spec_from_file_location('test_voice_routes', source)
            self.voice = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.voice)
        self.app = FastAPI()
        self.app.include_router(self.voice.router)
        self.client = TestClient(self.app)

    def test_voice_http_endpoints_require_real_login_before_provider_access(self):
        with patch.object(self.voice, '_active_provider') as provider, \
             patch.object(self.voice.httpx, 'AsyncClient') as client:
            for endpoint in ('provider', 'token'):
                for headers in ({}, {'Authorization': 'Bearer forged'}, self.headers(exp=1),
                                self.headers(aud='graph'), self.headers(iss='https://invalid.test')):
                    with self.subTest(endpoint=endpoint):
                        response = self.client.get('/api/v1/realtime/' + endpoint, headers=headers)
                        self.assertEqual(response.status_code, 401, response.text)
            provider.assert_not_called()
            client.assert_not_called()

    def test_signed_user_can_select_provider_and_request_ephemeral_token(self):
        with patch.object(self.voice, '_active_provider', return_value='gemini'):
            response = self.client.get('/api/v1/realtime/provider', headers=self.headers(tid='test-tenant'))
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['provider'], 'gemini')
        upstream = Mock(status_code=200)
        upstream.json.return_value = {'value': 'ephemeral-test'}
        client = AsyncMock()
        client.post.return_value = upstream
        with patch.object(self.voice.httpx, 'AsyncClient') as factory:
            factory.return_value.__aenter__.return_value = client
            response = self.client.get('/api/v1/realtime/token', headers=self.headers(tid='test-tenant'))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {'value': 'ephemeral-test'})
        client.post.assert_awaited_once()

    def test_incomplete_verified_identity_cannot_start_voice(self):
        with patch.object(self.voice, '_active_provider') as provider:
            response = self.client.get('/api/v1/realtime/provider', headers=self.headers(tid=None))
        self.assertEqual(response.status_code, 403)
        provider.assert_not_called()

    def test_websocket_rejects_missing_invalid_and_expired_tokens_before_vertex(self):
        for message in ({'type': 'user_identity'}, {'type': 'audio', 'data': 'AAAA'}, [],
                        {'type': 'user_identity', 'auth_token': 'forged'},
                        {'type': 'user_identity', 'auth_token': self.headers(exp=1)['Authorization'][7:]}):
            with self.subTest(message_type=type(message).__name__), \
                 patch.object(self.voice, '_get_vertex_access_token') as vertex:
                with self.client.websocket_connect('/api/v1/realtime/ws/voice') as socket:
                    self.assertEqual(socket.receive_json()['type'], 'ready')
                    socket.send_json(message)
                    self.assertEqual(socket.receive_json()['type'], 'error')
                    with self.assertRaises(WebSocketDisconnect) as closed:
                        socket.receive_json()
                    self.assertEqual(closed.exception.code, 1008)
                vertex.assert_not_called()

    def test_websocket_greeting_uses_signed_name_instead_of_browser_identity(self):
        class Provider:
            async def send(self, value):
                messages.append(value)
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            def __aiter__(self):
                return self
            async def __anext__(self):
                raise StopAsyncIteration

        messages = []
        self.settings.LOCATION = 'test-region'
        provider = Mock(return_value=Provider())
        with patch.dict(sys.modules, {'websockets': SimpleNamespace(connect=provider)}), \
             patch.object(self.voice, '_get_vertex_access_token', return_value='synthetic'):
            with self.client.websocket_connect('/api/v1/realtime/ws/voice') as socket:
                socket.receive_json()
                socket.send_json({'type': 'user_identity', 'user_id': 'victim@example.test',
                    'user_name': 'Forged', 'auth_token': self.headers(name='Verified Person',
                        tid='test-tenant', oid='owner')['Authorization'][7:]})
                self.assertEqual(socket.receive_json()['type'], 'session_end')
        self.assertIn('Hello Verified!', messages[0])
        self.assertNotIn('Forged', messages[0])
        self.assertIn('test-region-aiplatform.googleapis.com', provider.call_args.args[0])
        self.assertIn('locations/test-region', messages[0])

    def test_chat_adapter_forwards_bearer_and_requests_complete_answer(self):
        import asyncio
        upstream = Mock(status_code=200)
        upstream.json.return_value = {'response': 'Complete answer'}
        client = AsyncMock()
        client.post.return_value = upstream
        with patch.object(self.voice.httpx, 'AsyncClient') as factory:
            factory.return_value.__aenter__.return_value = client
            answer = asyncio.run(self.voice._ask_agent('Question', 'employee', 'User', 'thread', 'signed-token'))
        self.assertEqual(answer, 'Complete answer')
        self.assertEqual(client.post.call_args.kwargs['headers'], {'Authorization': 'Bearer signed-token'})
        self.assertFalse(client.post.call_args.kwargs['json']['stream'])


if __name__ == '__main__':
    unittest.main()
