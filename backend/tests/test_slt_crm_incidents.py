"""CreateIncident client: payload shape and response parsing. No real network
calls — every request goes through httpx.MockTransport."""
import json
import unittest
from unittest.mock import patch

import httpx

from core.config import settings
from services import slt_crm_incidents as crm

_URL = "https://crm.example.test/CreateIncident"

# Shape of the real staging response from 2026-11-18 (trimmed of noise).
_OK_BODY = {
    "Incident_Assign_Output": {
        "AssignId": "1-1VSMK1O",
        "Error_spcCode": {},
        "Error_spcMessage": {},
        "Process_spcInstance_spcId": "1-1VSMK1N",
        "ListOfSltIncidentIntObj": {"PubHlsIncident": {}},
    }
}

_ARGS = dict(
    description="Printer not working",
    incident_type="SOA",
    incident_sub_type="Resubmission issue",
    priority="Low",
    sltapi_user_id="016242",
)


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class CreateIncidentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for ctx in (
            patch.object(settings, "SLT_INCIDENT_CREATE_API_URL", _URL),
            patch.object(settings, "SLT_INCIDENT_API_CLIENT_ID", "test-client-id"),
        ):
            ctx.start()
            self.addCleanup(ctx.stop)

    async def test_success_sends_expected_payload_and_returns_assign_id(self):
        seen = {}

        def handler(request):
            seen["url"] = str(request.url)
            seen["client_id"] = request.headers.get("X-IBM-Client-Id")
            seen["cookie"] = request.headers.get("Cookie")
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json=_OK_BODY)

        async with _client(handler) as client:
            result = await crm.create_incident(**_ARGS, client=client)

        self.assertEqual(result.incident_id, "1-1VSMK1O")
        self.assertEqual(seen["url"], _URL)
        self.assertEqual(seen["client_id"], "test-client-id")
        self.assertIsNone(seen["cookie"])
        incident = seen["body"]["Incident_Assign_Input"]["ListOfSltIncidentIntObj"]["PubHlsIncident"]
        self.assertEqual(incident, {
            "Description": "Printer not working",
            "IncidentType": "SOA",
            "IncidentSubType": "Resubmission issue",
            "Priority": "Low",
            "Status": "Assigned",
            "SystemAsgnFlag": "Y",
            "SLTAPIUserID": "016242",
        })
        self.assertNotIn("ActivityUID", incident)

    async def test_crm_error_fields_raise(self):
        body = {"Incident_Assign_Output": {
            "AssignId": {}, "Error_spcCode": "SBL-123", "Error_spcMessage": "Invalid type"}}
        async with _client(lambda r: httpx.Response(200, json=body)) as client:
            with self.assertRaises(crm.CrmIncidentError):
                await crm.create_incident(**_ARGS, client=client)

    async def test_missing_assign_id_raises(self):
        body = {"Incident_Assign_Output": {"AssignId": {}, "Error_spcCode": {}}}
        async with _client(lambda r: httpx.Response(200, json=body)) as client:
            with self.assertRaises(crm.CrmIncidentError):
                await crm.create_incident(**_ARGS, client=client)

    async def test_non_json_body_raises(self):
        async with _client(lambda r: httpx.Response(200, text="<html>")) as client:
            with self.assertRaises(crm.CrmIncidentError):
                await crm.create_incident(**_ARGS, client=client)

    async def test_http_error_propagates(self):
        async with _client(lambda r: httpx.Response(500, text="boom")) as client:
            with self.assertRaises(httpx.HTTPStatusError):
                await crm.create_incident(**_ARGS, client=client)

    async def test_timeout_propagates(self):
        def handler(request):
            raise httpx.ReadTimeout("slow", request=request)

        async with _client(handler) as client:
            with self.assertRaises(httpx.TimeoutException):
                await crm.create_incident(**_ARGS, client=client)

    async def test_invalid_priority_rejected_before_any_call(self):
        def handler(request):
            raise AssertionError("must not call the CRM")

        async with _client(handler) as client:
            with self.assertRaises(ValueError):
                await crm.create_incident(**{**_ARGS, "priority": "Urgent"}, client=client)

    async def test_default_timeout_allows_slow_crm(self):
        # Staging CRM took 30-45s on 2026-11-19; 20s was too short.
        self.assertGreaterEqual(settings.SLT_INCIDENT_CREATE_TIMEOUT_SECONDS, 45)
        self.assertLess(settings.SLT_INCIDENT_CREATE_TIMEOUT_SECONDS, 60)

    async def test_not_configured_raises(self):
        with patch.object(settings, "SLT_INCIDENT_CREATE_API_URL", None):
            with self.assertRaises(crm.CrmNotConfiguredError):
                await crm.create_incident(**_ARGS)


if __name__ == "__main__":
    unittest.main()
