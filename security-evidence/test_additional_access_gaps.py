"""Evidence tests against source handlers with synthetic data only.

Passing means the documented vulnerability was reproduced, not that it is fixed.
Selected source definitions are compiled unchanged; database/LLM/CRM dependencies
are mocks. This is not a full application startup or production exploitation test.
"""
import ast
import logging
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, status
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from schemas.feedback import FeedbackRequest, FeedbackResponse


async def optional_identity():
    return None


def definitions(relative, names, namespace):
    tree = ast.parse((ROOT / relative).read_text(encoding='utf-8'))
    body = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    assert len(body) == len(names)
    namespace.update({'HTTPException': HTTPException, 'Depends': Depends, 'Query': Query,
                      'status': status, 'Optional': Optional})
    exec(compile(ast.Module(body=body, type_ignores=[]), relative, 'exec'), namespace)
    return namespace


class AdditionalAccessGaps(unittest.TestCase):
    def client(self, router):
        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    def test_helpdesk_anonymous_read_and_write_reach_storage(self):
        router = APIRouter(prefix='/api/v1/helpdesk_dev')
        listing = Mock(return_value=[{'ticket_id': 'SYNTHETIC', 'message': 'Synthetic private ticket'}])
        create = Mock(return_value={'ticket_id': 'SYNTHETIC'})
        definitions('backend/routers/helpdesk.py', ['get_tickets', 'post_ticket'], {
            'router': router, 'list_helpdesk_tickets': listing, 'create_helpdesk_ticket': create})
        client = self.client(router)
        response = client.get('/api/v1/helpdesk_dev/tickets')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['tickets'][0]['ticket_id'], 'SYNTHETIC')
        listing.assert_called_once_with(user_id=None, status=None, limit=100)
        response = client.post('/api/v1/helpdesk_dev/tickets', json={
            'userId': 'other-user', 'message': 'Synthetic', 'sub_category': 'test'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(create.call_args.kwargs['user_id'], 'other-user')

    def history_app(self):
        router = APIRouter(prefix='/api/v1/chat')
        graph = Mock()
        graph.get_state.return_value = SimpleNamespace(values={
            'user_id': 'owner-A', 'messages': [SimpleNamespace(type='human', content='Synthetic owner-A content')]})
        definitions('backend/routers/chat.py', ['_enforce_agent_auth', 'get_history'], {
            'router': router, 'get_optional_user': optional_identity,
            'PUBLIC_AGENT_IDS': frozenset({'aiexpo'}), 'get_agent_builder': Mock(),
            'get_compiled_sync_graph': Mock(return_value=graph), 'logger': logging.getLogger('audit')})
        app = FastAPI()
        app.include_router(router)
        return app

    def test_signed_in_other_user_reads_known_private_thread(self):
        app = self.history_app()
        app.dependency_overrides[optional_identity] = lambda: {'oid': 'other-user-B'}
        response = TestClient(app).get('/api/v1/chat/hr/owner-A-thread')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['messages'][0]['content'], 'Synthetic owner-A content')

    def test_public_agent_thread_read_requires_no_session_owner(self):
        response = TestClient(self.history_app()).get('/api/v1/chat/aiexpo/owner-A-thread')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['messages'][0]['content'], 'Synthetic owner-A content')

    def test_anonymous_private_agent_is_denied_control(self):
        response = TestClient(self.history_app()).get('/api/v1/chat/hr/owner-A-thread')
        self.assertEqual(response.status_code, 401)

    def test_anonymous_feedback_delete_reaches_database(self):
        router = APIRouter()
        database = MagicMock()
        definitions('backend/routers/feedback.py', ['delete_feedback'], {
            'router': router, 'FeedbackRequest': FeedbackRequest,
            'VALID_AGENTS': {'hr'}, '_ensure_feedback_table': Mock(),
            'psycopg': database, 'settings': SimpleNamespace(POSTGRES_URL='synthetic'),
            'logger': logging.getLogger('audit')})
        response = self.client(router).request('DELETE', '/api/v1/feedback', json={
            'agent_id': 'hr', 'thread_id': 'owner-A-thread', 'user_id': 'owner-A',
            'message_index': 0, 'rating': 'up'})
        self.assertEqual(response.status_code, 200)
        cursor = database.connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
        self.assertEqual(cursor.execute.call_args.args[1]['user_id'], 'owner-A')


if __name__ == '__main__':
    unittest.main(verbosity=2)
