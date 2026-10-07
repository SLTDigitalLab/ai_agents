"""Actual chat/feedback routes, signed JWTs and synthetic graph/database state."""
import importlib.util
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import test_admin_security as fixtures
from core import auth
from core.chat_access import chat_identity, scoped_thread_id


class ChatSecurityTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.AdminSecurityTests.setUpClass.__func__)
    headers = fixtures.AdminSecurityTests.headers

    def setUp(self):
        for context in [
            patch.dict(os.environ, {'AZURE_AUTH_ENABLED': 'false', 'ADMIN_EMAILS': 'supervisor@example.test'}),
            patch.object(auth, 'AZURE_CLIENT_ID', 'test-client'),
            patch.object(auth, 'AZURE_TENANT_ID', 'test-tenant'),
            patch.object(auth, 'ISSUER', 'https://login.microsoftonline.com/test-tenant/v2.0'),
            patch.object(auth, 'get_jwks', return_value={'keys': [self.public_jwk]}),
        ]:
            context.start()
            self.addCleanup(context.stop)
        self.state = {}
        self.graph = Mock()
        self.graph.get_state.side_effect = lambda config: SimpleNamespace(values=self.state.get(config['configurable']['thread_id'], {}))

        async def update(config, state):
            self.state[config['configurable']['thread_id']] = state
        self.graph.aupdate_state = AsyncMock(side_effect=update)
        self.classifier = AsyncMock(return_value=SimpleNamespace(action='BLOCK', reason='synthetic', sentiment='neutral'))
        self.record = Mock()
        self.database = MagicMock()
        self.cursor = self.database.connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
        self.cursor.fetchall.return_value = []
        module = fixtures.module
        stubs = {
            'core.config': module('core.config', settings=SimpleNamespace(POSTGRES_URL='synthetic')),
            'langchain_core.messages': module('langchain_core.messages',
                HumanMessage=lambda content: SimpleNamespace(type='human', content=content),
                AIMessage=lambda content: SimpleNamespace(type='ai', content=content)),
            'domain.registry': module('domain.registry', AGENT_BUILDERS={'hr': None, 'aiexpo': None},
                get_agent_builder=Mock(), get_compiled_sync_graph=Mock(return_value=self.graph),
                get_compiled_async_graph=AsyncMock(return_value=self.graph)),
            'domain.guardrails': module('domain.guardrails', classify_intent=self.classifier),
            'domain.archetypes.helpdesk_agent': module('domain.archetypes.helpdesk_agent', INTERNAL_LLM_TAG='internal'),
            'domain.tools.rag_tools': module('domain.tools.rag_tools', clear_thread_evidence=Mock(), consume_thread_evidence=Mock()),
            'services.sessions': module('services.sessions', record_session=self.record, ensure_sessions_table=Mock()),
            'langfuse.langchain': module('langfuse.langchain', CallbackHandler=Mock()),
            'langfuse': module('langfuse', Langfuse=Mock()),
            'psycopg': module('psycopg', connect=self.database.connect),
            'psycopg.rows': module('psycopg.rows', dict_row=Mock()),
        }
        self.app = FastAPI()
        with patch.dict(sys.modules, stubs):
            for name in ('chat', 'feedback'):
                path = Path(__file__).resolve().parents[1] / 'routers' / f'{name}.py'
                spec = importlib.util.spec_from_file_location('test_chat_routes_' + name, path)
                router = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(router)
                if name == 'chat':
                    self.chat_router = router
                if name == 'feedback':
                    router._ensure_feedback_table = Mock()
                self.app.include_router(router.router)
        self.client = TestClient(self.app)
        self.owner = self.headers('owner@example.test', oid='owner', tid='test-tenant')
        self.other = self.headers('other@example.test', oid='other', tid='test-tenant')
        self.guest = {'X-Chat-Session': 'a' * 64}
        self.body = {'message': 'Synthetic message', 'agent_id': 'hr', 'thread_id': 'known-thread',
                     'user_id': 'victim@example.test', 'user_name': 'Forged name',
                     'department': 'Forged department', 'job_title': 'Forged title'}

    def test_private_chat_and_history_require_real_login_even_when_auth_disabled(self):
        for headers in ({}, self.guest, {'Authorization': 'Bearer forged'}, self.headers(exp=1)):
            self.assertEqual(self.client.post('/api/v1/chat', json=self.body, headers=headers).status_code, 401)
            self.assertEqual(self.client.get('/api/v1/chat/hr/known-thread', headers=headers).status_code, 401)
        self.classifier.assert_not_called()
        self.graph.get_state.assert_not_called()
        self.record.assert_not_called()

    def test_body_identity_is_replaced_before_tracking_and_graph_update(self):
        response = self.client.post('/api/v1/chat', json=self.body, headers=self.owner)
        self.assertEqual(response.status_code, 200, response.text)
        stored = next(iter(self.state.values()))
        self.assertEqual(stored['user_id'], 'owner@example.test')
        self.assertNotEqual(stored['user_name'], 'Forged name')
        self.assertIsNone(stored['department'])
        self.assertIsNone(stored['job_title'])
        self.assertEqual(self.record.call_args.kwargs['user_id'], 'owner@example.test')
        self.assertEqual(self.record.call_args.kwargs['thread_id'], stored['thread_id'])

    def test_non_streaming_voice_response_preserves_verified_identity(self):
        response = self.client.post('/api/v1/chat', json={**self.body, 'stream': False}, headers=self.owner)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {'response': "I'm sorry, but I'm unable to help with that request."})
        self.assertEqual(next(iter(self.state.values()))['user_id'], 'owner@example.test')

    def test_streaming_remains_the_default(self):
        response = self.client.post('/api/v1/chat', json=self.body, headers=self.owner)
        self.assertTrue(response.headers['content-type'].startswith('text/event-stream'))

    def test_voice_gets_answer_without_hidden_visual_evidence(self):
        self.classifier.return_value = SimpleNamespace(action='PASS', reason='synthetic', sentiment='neutral')
        async def events(*args, **kwargs):
            yield {'event': 'on_chat_model_stream', 'metadata': {'langgraph_node': 'agent'},
                   'data': {'chunk': SimpleNamespace(content='Complete answer')}}
        self.graph.astream_events.side_effect = events
        evidence = '[[EVIDENCE_JSON]]{"items": [{"url": "private-image"}]}[[/EVIDENCE_JSON]]'
        with patch.object(self.chat_router, '_build_evidence_stream_chunk', return_value=evidence):
            response = self.client.post('/api/v1/chat', json={**self.body, 'stream': False}, headers=self.owner)
            self.assertEqual(response.json(), {'response': 'Complete answer'})
            response = self.client.post('/api/v1/chat', json=self.body, headers=self.owner)
            self.assertIn(evidence, response.text)

    def test_same_thread_id_cannot_read_or_overwrite_another_users_conversation(self):
        self.client.post('/api/v1/chat', json=self.body, headers=self.owner)
        owner_key = next(iter(self.state))
        self.assertTrue(self.client.get('/api/v1/chat/hr/known-thread', headers=self.owner).json()['messages'])
        self.assertEqual(self.client.get('/api/v1/chat/hr/known-thread', headers=self.other).json()['messages'], [])
        self.client.post('/api/v1/chat', json={**self.body, 'message': 'Other user'}, headers=self.other)
        self.assertEqual(len(self.state), 2)
        self.assertEqual(self.state[owner_key]['messages'][0].content, 'Synthetic message')
        # Supplying the stored ID cannot bypass the caller namespace either.
        self.assertEqual(self.client.get('/api/v1/chat/hr/' + owner_key, headers=self.other).json()['messages'], [])

    def test_guest_sessions_are_separate_and_preserve_own_history(self):
        body = {**self.body, 'agent_id': 'aiexpo'}
        self.assertEqual(self.client.post('/api/v1/chat', json=body).status_code, 401)
        self.assertEqual(self.client.post('/api/v1/chat', json=body, headers=self.guest).status_code, 200)
        self.assertTrue(self.client.get('/api/v1/chat/aiexpo/known-thread', headers=self.guest).json()['messages'])
        self.assertEqual(self.client.get('/api/v1/chat/aiexpo/known-thread', headers={'X-Chat-Session': 'b' * 64}).json()['messages'], [])
        stored = next(iter(self.state.values()))
        self.assertTrue(stored['user_id'].startswith('guest:'))
        self.assertNotIn('a' * 64, stored['user_id'])

    def test_legacy_threads_cannot_be_claimed_using_forged_owner(self):
        self.state['known-thread'] = {'user_id': 'owner@example.test', 'messages': [SimpleNamespace(type='human', content='Legacy secret')]}
        self.assertEqual(self.client.get('/api/v1/chat/hr/known-thread', headers=self.owner).json()['messages'], [])

    def test_missing_thread_and_incomplete_verified_identity_are_rejected(self):
        for thread in (None, '', ' '):
            self.assertEqual(self.client.post('/api/v1/chat', headers=self.owner, json={**self.body, 'thread_id': thread}).status_code, 422)
        without_thread = {key: value for key, value in self.body.items() if key != 'thread_id'}
        self.assertEqual(self.client.post('/api/v1/chat', headers=self.owner, json=without_thread).status_code, 422)
        for claims in ({'oid': None, 'tid': 'test-tenant'}, {'tid': None}, {'tid': 'test-tenant', 'preferred_username': None}):
            self.assertEqual(self.client.post('/api/v1/chat', headers=self.headers(**claims), json=self.body).status_code, 403)
        self.classifier.assert_not_called()

    def test_agent_tenant_and_identity_namespaces_are_distinct(self):
        first = chat_identity({'tid': 'tenantA', 'oid': 'same', 'email': 'same@example.test'}, None)
        second = chat_identity({'tid': 'tenantB', 'oid': 'same', 'email': 'same@example.test'}, None)
        self.assertNotEqual(scoped_thread_id('hr', 'same', first), scoped_thread_id('hr', 'same', second))
        self.assertNotEqual(scoped_thread_id('hr', 'same', first), scoped_thread_id('aiexpo', 'same', first))

    def test_feedback_requires_identity_and_uses_same_conversation_namespace(self):
        body = {'agent_id': 'hr', 'thread_id': 'known-thread', 'message_index': 0,
                'rating': 'up', 'user_id': 'victim@example.test'}
        for method in ('POST', 'DELETE'):
            self.assertEqual(self.client.request(method, '/api/v1/feedback', json=body).status_code, 401)
        self.assertEqual(self.client.get('/api/v1/feedback/hr/known-thread').status_code, 401)
        self.database.connect.assert_not_called()
        self.client.post('/api/v1/chat', json=self.body, headers=self.owner)
        response = self.client.request('DELETE', '/api/v1/feedback', json=body, headers=self.owner)
        self.assertEqual(response.status_code, 200, response.text)
        params = self.cursor.execute.call_args.args[1]
        self.assertEqual(params['thread_id'], next(iter(self.state)))
        self.assertEqual(params['user_id'], 'owner@example.test')
        self.client.get('/api/v1/feedback/hr/known-thread', headers=self.other)
        self.assertNotEqual(self.cursor.execute.call_args.args[1]['thread_id'], next(iter(self.state)))


if __name__ == '__main__':
    unittest.main()
