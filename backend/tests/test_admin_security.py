"""Real HTTP routes and JWT validation, with external services replaced by mocks.

Run from the repository root: python -m unittest discover -s backend/tests -v
No production data, Microsoft login, or database connection is used.
"""
import ast
import base64
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
import types
import unittest
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwk, jwt

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from core import auth


def module(name, **attributes):
    result = types.ModuleType(name)
    result.__dict__.update(attributes)
    return result


class AdminSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.pem = cls.key.private_bytes(serialization.Encoding.PEM,
                                       serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption())
        public = cls.key.public_key().public_bytes(serialization.Encoding.PEM,
                                                   serialization.PublicFormat.SubjectPublicKeyInfo)
        cls.public_jwk = jwk.construct(public, 'RS256').to_dict()
        cls.public_jwk['kid'] = 'test-key'

    def setUp(self):
        self.env = patch.dict(os.environ, {
            'ADMIN_EMAILS': 'supervisor@example.test,scoped@example.test',
            'VITE_ADMIN_EMAILS': 'legacy@example.test',
            'ADMIN_AGENT_MAP': json.dumps({'supervisor@example.test': ['*'],
                                          'scoped@example.test': ['hr']}),
            'AZURE_AUTH_ENABLED': 'true',
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        for name, value in [('AZURE_CLIENT_ID', 'test-client'), ('AZURE_TENANT_ID', 'test-tenant'),
                            ('ISSUER', 'https://login.microsoftonline.com/test-tenant/v2.0')]:
            p = patch.object(auth, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(auth, 'get_jwks', return_value={'keys': [self.public_jwk]})
        p.start()
        self.addCleanup(p.stop)
        self.ingestion = Mock()
        self.ingestion.list_kb_documents.return_value = {'status': 'success', 'documents': []}
        self.ingestion.delete_agent_kb.return_value = {'status': 'success'}
        self.status = Mock(get_status=Mock(return_value={'active': False}))
        self.database = Mock(side_effect=AssertionError('Database must not be contacted'))
        stubs = {
            'core.config': module('core.config', settings=Mock()),
            'services.ingestion': module('services.ingestion', IngestionService=lambda: self.ingestion),
            'services.ingestion_slm': module('services.ingestion_slm', slm_ingestion_service=Mock()),
            'services.ingestion_status': self.status,
            'services.sessions': module('services.sessions', get_sessions_users=Mock(), ensure_sessions_table=Mock()),
            'domain.tools.api_tools': module('domain.tools.api_tools', LEAVE_BALANCE_API_URL='https://invalid.test'),
            'domain.registry': module('domain.registry', AGENT_BUILDERS={'hr': None}, get_compiled_sync_graph=Mock()),
            'psycopg': module('psycopg', connect=self.database),
            'psycopg.rows': module('psycopg.rows', dict_row=Mock()),
            'langfuse': module('langfuse', Langfuse=Mock()),
        }
        self.app = FastAPI()
        with patch.dict(sys.modules, stubs):
            # Import the actual routers, without importing live ingestion/database clients.
            for name in ['admin', 'admin_dashboard', 'feedback']:
                spec = importlib.util.spec_from_file_location(f'security_test_{name}', BACKEND / 'routers' / f'{name}.py')
                router_module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(router_module)
                if name == 'admin':
                    router_module.ingestion_status = self.status
                self.app.include_router(router_module.router)
        self.client = TestClient(self.app)

    def headers(self, email='supervisor@example.test', **claims):
        payload = {'aud': 'test-client', 'iss': auth.ISSUER, 'exp': int(time.time()) + 600,
                   'oid': 'test-user', 'preferred_username': email}
        payload.update(claims)
        for key in list(payload):
            if payload[key] is None:
                del payload[key]
        token = jwt.encode(payload, self.pem, algorithm='RS256', headers={'kid': 'test-key'})
        return {'Authorization': f'Bearer {token}'}

    def admin_routes(self):
        return [r for r in self.app.routes if r.path.startswith('/api/v1/admin')]

    def test_every_admin_route_rejects_anonymous_and_non_admin(self):
        for route in self.admin_routes():
            path = route.path.replace('{agent}', 'hr').replace('{session_id}', 'test')
            method = next(iter(route.methods))
            for headers, expected in [({}, 401), (self.headers('other@example.test'), 403)]:
                with self.subTest(path=path, expected=expected):
                    response = self.client.request(method, path, headers=headers, json={})
                    self.assertEqual(response.status_code, expected, response.text)
        self.database.assert_not_called()
        self.assertEqual(self.ingestion.mock_calls, [])

    def test_invalid_expired_wrong_audience_and_wrong_issuer_tokens(self):
        bad_headers = [{'Authorization': 'Bearer forged'}, self.headers(exp=1),
                       self.headers(aud='graph'), self.headers(iss='https://another-tenant.test'),
                       self.headers(exp=None), self.headers(aud=None)]
        for headers in bad_headers:
            with self.subTest(headers_kind='invalid token'):
                self.assertEqual(self.client.get('/api/v1/admin/ingestion-status', headers=headers).status_code, 401)

    def test_forged_signature_is_rejected(self):
        valid = self.headers()['Authorization'].split(' ', 1)[1]
        parts = valid.split('.')
        signature = bytearray(base64.urlsafe_b64decode(parts[2] + '=='))
        signature[0] ^= 1
        parts[2] = base64.urlsafe_b64encode(signature).decode().rstrip('=')
        response = self.client.get('/api/v1/admin/ingestion-status',
                                   headers={'Authorization': 'Bearer ' + '.'.join(parts)})
        self.assertEqual(response.status_code, 401)

    def test_admin_auth_cannot_be_disabled(self):
        os.environ['AZURE_AUTH_ENABLED'] = 'false'
        self.assertEqual(self.client.get('/api/v1/admin/ingestion-status').status_code, 401)

    def test_approved_admin_can_read_and_use_authorized_agent(self):
        self.assertEqual(self.client.get('/api/v1/admin/ingestion-status', headers=self.headers()).status_code, 200)
        response = self.client.post('/api/v1/admin/kb-documents', headers=self.headers('scoped@example.test'),
                                    json={'agent_name': 'hr', 'user_email': 'untrusted@example.test'})
        self.assertEqual(response.status_code, 200, response.text)
        self.ingestion.list_kb_documents.assert_called_once_with('hr')

    def test_supervisor_can_use_wildcard_permission_without_body_email(self):
        response = self.client.post('/api/v1/admin/delete-agent-kb', headers=self.headers(),
                                    json={'agent_name': 'finance'})
        self.assertEqual(response.status_code, 200, response.text)
        self.ingestion.delete_agent_kb.assert_called_once_with('finance')

    def test_forged_supervisor_email_cannot_grant_agent_access(self):
        response = self.client.post('/api/v1/admin/delete-agent-kb', headers=self.headers('scoped@example.test'),
                                    json={'agent_name': 'finance', 'user_email': 'supervisor@example.test'})
        self.assertEqual(response.status_code, 403)
        self.ingestion.delete_agent_kb.assert_not_called()

    def test_collection_override_and_sharepoint_enforce_agent_permissions(self):
        for endpoint, body in [
            ('ingest-url', {'url': 'https://example.test', 'agent_name': 'hr', 'collection_name': 'finance'}),
            ('ingest-sharepoint', {'site_url': 'https://example.test', 'folder_path': '/', 'token': 'graph', 'agent_name': 'finance'}),
        ]:
            with self.subTest(endpoint=endpoint):
                response = self.client.post('/api/v1/admin/' + endpoint, headers=self.headers('scoped@example.test'), json=body)
                self.assertEqual(response.status_code, 403, response.text)
        self.status.start.assert_not_called()

    def test_empty_allowlist_fails_closed_and_legacy_configuration_works(self):
        os.environ['ADMIN_EMAILS'] = ''
        self.assertEqual(self.client.get('/api/v1/admin/ingestion-status', headers=self.headers()).status_code, 403)
        del os.environ['ADMIN_EMAILS']
        self.assertEqual(self.client.get('/api/v1/admin/ingestion-status', headers=self.headers('legacy@example.test')).status_code, 200)

    def test_malformed_agent_permissions_fail_closed(self):
        for mapping in ['not json', '[]', '{"supervisor@example.test": "*"}']:
            os.environ['ADMIN_AGENT_MAP'] = mapping
            response = self.client.post('/api/v1/admin/delete-agent-kb', headers=self.headers(), json={'agent_name': 'hr'})
            self.assertEqual(response.status_code, 403)
        self.ingestion.delete_agent_kb.assert_not_called()

    def test_refresh_script_requires_and_sends_token(self):
        # Exercise the real request helper without loading the maintenance job
        # (which reads deployment settings and imports database clients).
        source = ast.parse((BACKEND / 'scripts' / 'monthly_kb_refresh.py').read_text(encoding='utf-8'))
        helper = next(n for n in source.body if isinstance(n, ast.FunctionDef) and n.name == 'request_json')
        requests_mock = Mock()
        requests_mock.request.return_value.status_code = 200
        requests_mock.request.return_value.json.return_value = {'active': False}
        namespace = {'os': os, 'requests': requests_mock, 'Any': object}
        exec(compile(ast.Module(body=[helper], type_ignores=[]), '<refresh helper>', 'exec'), namespace)
        with patch.dict(os.environ, {'ADMIN_API_TOKEN': ''}):
            with self.assertRaises(RuntimeError):
                namespace['request_json']('GET', 'https://example.test/api/v1/admin/ingestion-status')
        requests_mock.request.assert_not_called()
        with patch.dict(os.environ, {'ADMIN_API_TOKEN': 'test-token'}):
            namespace['request_json']('GET', 'https://example.test/api/v1/admin/ingestion-status')
        self.assertEqual(requests_mock.request.call_args.kwargs['headers'],
                         {'Authorization': 'Bearer test-token'})


if __name__ == '__main__':
    unittest.main()
