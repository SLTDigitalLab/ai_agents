"""Exercise the real router without importing unrelated database/model services."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient


class NapsterSessionTests(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).resolve().parents[1] / "routers/napster.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        tree.body = [n for n in tree.body if not (isinstance(n, ast.ImportFrom) and n.module == "core.config")]
        self.settings = SimpleNamespace(NAPSTER_API_KEY="test-private-key", NAPSTER_AGENT_ID="existing-agent")
        self.ns = {"settings": self.settings}
        exec(compile(tree, str(source), "exec"), self.ns)
        app = FastAPI()
        app.include_router(self.ns["router"])
        self.client = TestClient(app)
        self.mock = AsyncMock()
        self.mock.__aenter__.return_value = self.mock
        self.mock.get.side_effect = [self.upstream({
            "companionId": "existing-companion", "voiceId": "existing-voice",
            "providerSettings": {"temperature": 0.6},
        }), self.upstream({"flow": "implicit", "data": {"name": "answer", "parameters": {
            "properties": {"user_message": {"type": "string"}}, "required": ["user_message"],
        }}})]
        self.mock.post.return_value = self.upstream({"token": "temporary", "private": "test-private-key"})

    def upstream(self, data, status=200):
        return httpx.Response(status, json=data, request=httpx.Request("POST", "https://companion-api.napster.com/public/connections"))

    def request(self):
        with patch.object(httpx, "AsyncClient", return_value=self.mock):
            return self.client.post("/api/napster/session")

    def test_existing_avatar_and_exact_function_registration(self):
        response = self.request()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"token": "temporary"})
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(self.mock.post.call_args.args[0], "https://companion-api.napster.com/public/connections")
        payload = self.mock.post.call_args.kwargs["json"]
        self.assertEqual(payload["companionId"], "existing-companion")
        self.assertEqual(payload["providerConfig"]["voiceId"], "existing-voice")
        self.assertEqual(payload["functions"], ["answer"])
        self.assertFalse(payload["useWebSearch"])
        self.assertNotIn("initialSpeech", payload)
        self.assertIn("EVERY customer utterance", payload["providerConfig"]["settings"]["instructions"])
        self.assertEqual(payload["providerConfig"]["settings"]["turnDetection"], {
            "threshold": 0.9,
            "prefix_padding_ms": 400,
            "silence_duration_ms": 500,
        })
        self.assertEqual(
            payload["providerConfig"]["settings"]["noiseReduction"],
            {"type": "nearField"},
        )
        self.assertEqual(self.mock.get.call_count, 2)

    def test_missing_configuration_does_not_call_provider(self):
        self.settings.NAPSTER_API_KEY = ""
        self.assertEqual(self.request().status_code, 503)
        self.mock.get.assert_not_called()

    def test_invalid_agent_id(self):
        self.settings.NAPSTER_AGENT_ID = "../another-path"
        self.assertEqual(self.request().status_code, 503)
        self.mock.get.assert_not_called()

    def test_invalid_token_is_rejected(self):
        for value in [None, "", 12, []]:
            with self.subTest(value=value):
                self.setUp()
                self.mock.post.return_value = self.upstream({"token": value})
                self.assertEqual(self.request().status_code, 502)

    def test_upstream_errors_do_not_expose_secrets(self):
        for status in [400, 401, 403, 404, 429, 500, 302]:
            with self.subTest(status=status):
                self.setUp()
                self.mock.post.return_value = self.upstream({"detail": "test-private-key"}, status)
                response = self.request()
                self.assertGreaterEqual(response.status_code, 400)
                self.assertNotIn("test-private-key", response.text)

    def test_missing_companion_never_creates_an_avatar(self):
        self.mock.get.side_effect = [self.upstream({})]
        self.assertEqual(self.request().status_code, 502)
        self.mock.post.assert_not_called()

    def test_wrong_function_contract_rejected(self):
        self.mock.get.side_effect = [self.upstream({"companionId": "existing"}), self.upstream({"flow": "explicit"})]
        self.assertEqual(self.request().status_code, 503)
        self.mock.post.assert_not_called()

    def test_timeout(self):
        self.mock.get.side_effect = httpx.ReadTimeout("test-private-key")
        self.assertEqual(self.request().status_code, 504)

    def test_network_failure(self):
        self.mock.get.side_effect = httpx.ConnectError("test-private-key")
        self.assertEqual(self.request().status_code, 502)


if __name__ == "__main__":
    unittest.main()
