"""POST /api/v1/helpdesk_dev/incidents — real route + signed JWTs; the CRM
client and the database are mocked (no network, no Postgres)."""
import importlib.util
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

import test_admin_security as admin_fixtures
from core import auth
from services import slt_crm_incidents as crm

EMPLOYEE = '016242@intranet.slt.com.lk'
FORM = {
    'description': 'Printer on floor 3 not printing',
    'incident_type': 'SOA',
    'incident_sub_type': 'Resubmission issue',
    'priority': 'Low',
}


class HelpdeskIncidentTests(unittest.TestCase):
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
            spec = importlib.util.spec_from_file_location('helpdesk_incident_routes', source)
            routes = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(routes)
        self.create_incident = AsyncMock(
            return_value=crm.CreatedIncident(incident_id='1-1VSMK1O', raw={}))
        patcher = patch.object(routes.crm, 'create_incident', self.create_incident)
        patcher.start()
        self.addCleanup(patcher.stop)
        app = FastAPI()
        app.include_router(routes.router)
        self.client = TestClient(app)
        self.url = '/api/v1/helpdesk_dev/incidents'
        self.employee_headers = self.headers(EMPLOYEE)

    def post(self, body=None, headers=None):
        return self.client.post(self.url, json=FORM if body is None else body,
                                headers=self.employee_headers if headers is None else headers)

    def test_success_calls_crm_with_employee_number_and_saves_locally(self):
        response = self.post()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['incident_id'], '1-1VSMK1O')
        self.create_incident.assert_awaited_once_with(
            description=FORM['description'], incident_type='SOA',
            incident_sub_type='Resubmission issue', priority='Low', sltapi_user_id='016242')
        self.service.create_helpdesk_ticket.assert_called_once_with(
            user_id=EMPLOYEE, message=FORM['description'], ticket_id='1-1VSMK1O',
            status='open', main_category='SOA', sub_category='Resubmission issue')

    def test_whitespace_is_trimmed(self):
        body = {**FORM, 'description': '  Printer broken  ', 'incident_type': ' SOA '}
        self.assertEqual(self.post(body).status_code, 200)
        kwargs = self.create_incident.call_args.kwargs
        self.assertEqual((kwargs['description'], kwargs['incident_type']), ('Printer broken', 'SOA'))

    def test_identity_comes_only_from_token(self):
        body = {**FORM, 'sltapi_user_id': '999999', 'userId': 'victim@intranet.slt.com.lk'}
        self.assertEqual(self.post(body).status_code, 200)
        self.assertEqual(self.create_incident.call_args.kwargs['sltapi_user_id'], '016242')

    def test_rejects_missing_or_invalid_token(self):
        for headers in ({}, {'Authorization': 'Bearer forged'}, self.headers(EMPLOYEE, exp=1)):
            with self.subTest(headers=headers):
                self.assertEqual(self.post(headers=headers).status_code, 401)
        self.create_incident.assert_not_awaited()

    def test_non_slt_account_is_rejected_before_crm(self):
        for login in ('owner@example.test', 'bad.name@intranet.slt.com.lk',
                      '016242@intranet.slt.com.lk.evil.test'):
            with self.subTest(login=login):
                self.assertEqual(self.post(headers=self.headers(login)).status_code, 403)
        self.create_incident.assert_not_awaited()

    def test_invalid_form_is_rejected_before_crm(self):
        cases = [
            {**FORM, 'description': '   '},
            {**FORM, 'incident_type': ''},
            {**FORM, 'priority': 'Urgent'},
            {**FORM, 'description': 'x' * 256},
            {k: v for k, v in FORM.items() if k != 'incident_sub_type'},
        ]
        for body in cases:
            with self.subTest(body=body):
                self.assertEqual(self.post(body).status_code, 422)
        self.create_incident.assert_not_awaited()
        self.service.create_helpdesk_ticket.assert_not_called()

    def test_crm_failures_map_to_friendly_errors_and_skip_local_save(self):
        request = httpx.Request('POST', 'https://crm.example.test')
        cases = [
            (crm.CrmNotConfiguredError('missing'), 503),
            (httpx.ReadTimeout('slow', request=request), 504),
            (crm.CrmIncidentError('code=SBL-1'), 502),
            (httpx.ConnectError('down', request=request), 502),
        ]
        for error, expected in cases:
            with self.subTest(error=type(error).__name__):
                self.create_incident.side_effect = error
                response = self.post()
                self.assertEqual(response.status_code, expected, response.text)
                self.assertNotIn('SBL', response.text)
        self.service.create_helpdesk_ticket.assert_not_called()

    def test_timeout_message_warns_against_immediate_retry(self):
        # The CRM may have created the incident before we gave up waiting.
        request = httpx.Request('POST', 'https://crm.example.test')
        self.create_incident.side_effect = httpx.ReadTimeout('slow', request=request)
        detail = self.post().json()['detail']
        self.assertIn('may still have been created', detail)
        self.assertIn("don't submit again", detail)

    def test_local_save_failure_still_returns_crm_ticket(self):
        self.service.create_helpdesk_ticket.side_effect = RuntimeError('db down')
        response = self.post()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['incident_id'], '1-1VSMK1O')


if __name__ == '__main__':
    unittest.main()
