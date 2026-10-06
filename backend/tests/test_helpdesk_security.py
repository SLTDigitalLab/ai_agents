"""Real helpdesk HTTP routes and signed JWTs; database calls are mocked."""
import importlib.util
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import test_admin_security as admin_fixtures
from core import auth


class HelpdeskSecurityTests(unittest.TestCase):
    # Reuse only the JWT fixture, not the admin test cases or app fixture.
    setUpClass = classmethod(admin_fixtures.AdminSecurityTests.setUpClass.__func__)
    headers = admin_fixtures.AdminSecurityTests.headers

    def setUp(self):
        for context in [
            patch.dict(os.environ, {'ADMIN_EMAILS': 'supervisor@example.test',
                                    'AZURE_AUTH_ENABLED': 'true'}),
            patch.object(auth, 'AZURE_CLIENT_ID', 'test-client'),
            patch.object(auth, 'AZURE_TENANT_ID', 'test-tenant'),
            patch.object(auth, 'ISSUER', 'https://login.microsoftonline.com/test-tenant/v2.0'),
            patch.object(auth, 'get_jwks', return_value={'keys': [self.public_jwk]}),
        ]:
            context.start()
            self.addCleanup(context.stop)
        self.service = types.ModuleType('services.helpdesk_tickets')
        for name in ('create_helpdesk_ticket', 'get_helpdesk_ticket', 'list_helpdesk_tickets',
                     'create_solved_ticket', 'get_solved_ticket', 'list_solved_tickets',
                     'create_category', 'get_category', 'list_categories'):
            setattr(self.service, name, Mock(return_value=[] if name.startswith('list_') else {}))
        source = Path(__file__).resolve().parents[1] / 'routers' / 'helpdesk.py'
        with patch.dict(sys.modules, {'services.helpdesk_tickets': self.service}):
            spec = importlib.util.spec_from_file_location('helpdesk_security_routes', source)
            routes = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(routes)
        self.app = FastAPI()
        self.app.include_router(routes.router)
        self.client = TestClient(self.app)
        self.base = '/api/v1/helpdesk_dev'
        self.owner_headers = self.headers('owner@example.test')

    def assert_no_database_calls(self):
        for value in vars(self.service).values():
            if isinstance(value, Mock):
                value.assert_not_called()

    def test_every_route_rejects_missing_and_invalid_tokens_even_when_chat_auth_disabled(self):
        os.environ['AZURE_AUTH_ENABLED'] = 'false'
        for route in self.app.routes:
            if not route.path.startswith(self.base):
                continue
            path = route.path.replace('{ticket_id}', 'TKT-1').replace('{solved_id}', '1').replace('{category_id}', '1')
            for headers in ({}, {'Authorization': 'Bearer forged'}, self.headers(exp=1),
                            self.headers(aud='wrong-client'), self.headers(iss='wrong-issuer')):
                with self.subTest(path=path, headers_kind='missing or invalid'):
                    response = self.client.request(next(iter(route.methods)), path, headers=headers, json={})
                    self.assertEqual(response.status_code, 401, response.text)
        self.assert_no_database_calls()

    def test_list_defaults_to_verified_owner_and_cannot_be_unscoped(self):
        for params in ({}, {'userId': ' OWNER@example.test '}):
            response = self.client.get(self.base + '/tickets', params=params, headers=self.owner_headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.service.list_helpdesk_tickets.assert_called_with(user_id='owner@example.test', status=None, limit=100)
        self.service.list_helpdesk_tickets.reset_mock()
        for target, expected in [('other@example.test', 403), ('', 422), (' ', 422)]:
            response = self.client.get(self.base + '/tickets', params={'userId': target}, headers=self.owner_headers)
            self.assertEqual(response.status_code, expected, response.text)
        self.service.list_helpdesk_tickets.assert_not_called()

    def test_missing_identity_fails_closed(self):
        for claims in ({'oid': None}, {'preferred_username': None, 'email': None}):
            response = self.client.get(self.base + '/tickets', headers=self.headers(**claims))
            self.assertEqual(response.status_code, 403, response.text)
        self.assert_no_database_calls()

    def test_ticket_read_is_owner_or_admin_only(self):
        self.service.get_helpdesk_ticket.return_value = {'ticket_id': 'TKT-1', 'userId': 'owner@example.test'}
        for headers, expected in [(self.owner_headers, 200), (self.headers('other@example.test'), 404),
                                  (self.headers(), 200)]:
            response = self.client.get(self.base + '/tickets/TKT-1', headers=headers)
            self.assertEqual(response.status_code, expected, response.text)

    def test_create_uses_verified_owner_and_rejects_spoofing_and_managed_fields(self):
        body = {'message': 'Synthetic test', 'sub_category': 'Network'}
        response = self.client.post(self.base + '/tickets', headers=self.owner_headers, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.service.create_helpdesk_ticket.call_args.kwargs['user_id'], 'owner@example.test')
        self.service.create_helpdesk_ticket.reset_mock()
        for extra in ({'userId': 'victim@example.test'}, {'status': 'solved'}, {'ticket_id': 'TKT-2'},
                      {'duplicate_check': 'closed'}, {'need_more_informations': 'override'},
                      {'updated_main_category': 'override'}, {'updated_sub_category': 'override'}):
            response = self.client.post(self.base + '/tickets', headers=self.owner_headers, json={**body, **extra})
            self.assertEqual(response.status_code, 403, response.text)
        for invalid in ('', [], {}):
            response = self.client.post(self.base + '/tickets', headers=self.owner_headers,
                                        json={**body, 'userId': invalid})
            self.assertEqual(response.status_code, 422, response.text)
        self.service.create_helpdesk_ticket.assert_not_called()

    def test_admin_can_explicitly_select_and_create_for_another_user(self):
        response = self.client.get(self.base + '/tickets', headers=self.headers(),
                                   params={'userId': 'other@example.test', 'limit': 1})
        self.assertEqual(response.status_code, 200, response.text)
        self.service.list_helpdesk_tickets.assert_called_once_with(user_id='other@example.test', status=None, limit=1)
        response = self.client.post(self.base + '/tickets', headers=self.headers(),
                                    json={'userId': 'other@example.test', 'message': 'Synthetic',
                                          'sub_category': 'Network', 'status': 'open'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.service.create_helpdesk_ticket.call_args.kwargs['user_id'], 'other@example.test')

    def test_knowledge_routes_require_admin_and_allow_admin(self):
        cases = [('GET', '/solved-tickets', {}), ('GET', '/solved-tickets/1', {}),
                 ('POST', '/solved-tickets', {'requirements': 'Synthetic', 'answer': 'Synthetic'}),
                 ('GET', '/categories', {}), ('GET', '/categories/1', {}),
                 ('POST', '/categories', {'category_id': '1', 'category_name': 'Synthetic', 'subcategory': 'Test'})]
        self.service.get_solved_ticket.return_value = {'id': 1}
        self.service.get_category.return_value = {'category_id': '1'}
        for method, path, body in cases:
            response = self.client.request(method, self.base + path, headers=self.owner_headers, json=body)
            self.assertEqual(response.status_code, 403, response.text)
        self.assert_no_database_calls()
        for method, path, body in cases:
            response = self.client.request(method, self.base + path, headers=self.headers(), json=body)
            self.assertEqual(response.status_code, 200, response.text)

    def test_empty_admin_allowlist_denies_cross_user_access(self):
        os.environ['ADMIN_EMAILS'] = ''
        response = self.client.get(self.base + '/tickets', headers=self.headers(), params={'userId': 'other@example.test'})
        self.assertEqual(response.status_code, 403, response.text)
        self.assert_no_database_calls()


if __name__ == '__main__':
    unittest.main()
