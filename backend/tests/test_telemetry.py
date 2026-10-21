"""Isolated HTTP/ASGI tests: no databases, AI keys or production access."""
import asyncio
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.telemetry import HttpMetrics, HttpMetricsMiddleware, install_http_metrics

TOKEN = 'synthetic-test-only-token-' + 'x' * 32


class TelemetryTests(unittest.TestCase):
    def make_app(self, **env):
        app = FastAPI()

        @app.get('/api/v1/items/{item_id}')
        def item(item_id: str):
            return {'ok': True}

        @app.get('/api/v1/protected')
        def protected():
            raise HTTPException(401, 'Not authenticated')

        @app.get('/api/v1/crash')
        def crash():
            raise RuntimeError('synthetic error')

        @app.get('/api/v1/health')
        def health():
            return {'ok': True}

        with patch.dict(os.environ, {'METRICS_ENABLED': 'true', 'METRICS_BEARER_TOKEN': TOKEN,
                                    'WEB_CONCURRENCY': '1', 'PROMETHEUS_MULTIPROC_DIR': '', **env}):
            install_http_metrics(app)
        return app

    def test_success_failure_privacy_and_scrape_auth(self):
        app = self.make_app()
        with TestClient(app, raise_server_exceptions=False) as client:
            self.assertEqual(client.get('/metrics').status_code, 401)
            self.assertEqual(client.get('/metrics', headers={'Authorization': 'Bearer wrong'}).status_code, 401)
            self.assertEqual(client.get('/api/v1/items/private-id?email=private@example.test').status_code, 200)
            self.assertEqual(client.get('/api/v1/protected').status_code, 401)
            self.assertEqual(client.get('/api/v1/crash').status_code, 500)
            client.get('/api/v1/unknown-private-id')
            client.get('/api/v1/health')
            client.get('/')
            result = client.get('/metrics', headers={'Authorization': 'Bearer ' + TOKEN})
            self.assertEqual(result.status_code, 200)
            self.assertIn('text/plain', result.headers['content-type'])
            self.assertEqual(result.headers['cache-control'], 'no-store')
            body = result.text
            for secret in ('private-id', 'private@example.test', TOKEN, 'unknown-private-id'):
                self.assertNotIn(secret, body)
            r = app.state.http_metrics.registry
            for route, status in [('/api/v1/items/{item_id}', '200'), ('/api/v1/protected', '401'),
                                  ('/api/v1/crash', '500'), ('unmatched', '404')]:
                self.assertEqual(r.get_sample_value('ask_slt_http_requests_total',
                                 {'method': 'GET', 'route': route, 'status_code': status}), 1)
                self.assertEqual(r.get_sample_value('ask_slt_http_request_duration_seconds_count',
                                 {'method': 'GET', 'route': route}), 1)
            self.assertEqual(r.get_sample_value('ask_slt_http_requests_in_progress', {'method': 'GET'}), 0)
            self.assertGreater(r.get_sample_value('ask_slt_http_last_request_timestamp_seconds'), 0)
            self.assertNotIn('route="/api/v1/health"', body)
            self.assertNotIn('route="/metrics"', body)

    def test_disabled_and_invalid_configuration(self):
        with TestClient(self.make_app(METRICS_ENABLED='false')) as client:
            self.assertEqual(client.get('/metrics').status_code, 404)
        for env in ({'METRICS_BEARER_TOKEN': ''}, {'WEB_CONCURRENCY': '2'},
                    {'PROMETHEUS_MULTIPROC_DIR': '/tmp/metrics'}):
            with self.assertRaises(RuntimeError):
                self.make_app(**env)

    def test_stream_completion_measured_before_background_work(self):
        metrics = HttpMetrics()

        async def app(scope, receive, send):
            self.assertEqual(metrics.registry.get_sample_value('ask_slt_http_requests_in_progress', {'method': 'POST'}), 1)
            await send({'type': 'http.response.start', 'status': 200, 'headers': []})
            await send({'type': 'http.response.body', 'body': b'chunk', 'more_body': True})
            self.assertEqual(metrics.registry.get_sample_value('ask_slt_http_requests_in_progress', {'method': 'POST'}), 1)
            await send({'type': 'http.response.body', 'body': b'', 'more_body': False})
            self.assertEqual(metrics.registry.get_sample_value('ask_slt_http_requests_in_progress', {'method': 'POST'}), 0)
            raise RuntimeError('background failure must not double count completed response')

        async def run():
            async def receive():
                return {'type': 'http.request', 'body': b''}
            async def send(message):
                pass
            with self.assertRaises(RuntimeError):
                await HttpMetricsMiddleware(app, metrics)({'type': 'http', 'method': 'POST', 'path': '/api/v1/chat'}, receive, send)
        asyncio.run(run())
        self.assertEqual(metrics.registry.get_sample_value('ask_slt_http_terminations_total',
                         {'method': 'POST', 'route': 'unmatched', 'outcome': 'completed'}), 1)

    def test_aborted_stream_and_cancellation_cleanup(self):
        for mode in ('exception', 'cancelled', 'disconnected'):
            with self.subTest(mode=mode):
                metrics = HttpMetrics()

                async def app(scope, receive, send):
                    if mode == 'cancelled':
                        raise asyncio.CancelledError()
                    await send({'type': 'http.response.start', 'status': 200, 'headers': []})
                    if mode == 'exception':
                        raise RuntimeError('stream failed')
                    await receive()

                async def run():
                    async def receive():
                        return {'type': 'http.disconnect'}
                    async def send(message):
                        pass
                    try:
                        await HttpMetricsMiddleware(app, metrics)({'type': 'http', 'method': 'CUSTOM', 'path': '/api/v1/chat'}, receive, send)
                    except (RuntimeError, asyncio.CancelledError):
                        pass
                asyncio.run(run())
                r = metrics.registry
                self.assertEqual(r.get_sample_value('ask_slt_http_requests_in_progress', {'method': 'OTHER'}), 0)
                self.assertEqual(r.get_sample_value('ask_slt_http_requests_total',
                                 {'method': 'OTHER', 'route': 'unmatched', 'status_code': 'none' if mode == 'cancelled' else '200'}), 1)
                self.assertEqual(r.get_sample_value('ask_slt_http_terminations_total',
                                 {'method': 'OTHER', 'route': 'unmatched', 'outcome': mode}), 1)

    def test_websocket_passthrough(self):
        metrics = HttpMetrics()
        async def app(scope, receive, send):
            await send({'type': 'websocket.close'})
        async def run():
            sent = []
            async def send(message):
                sent.append(message)
            await HttpMetricsMiddleware(app, metrics)({'type': 'websocket', 'path': '/api/v1/realtime/ws/voice'}, None, send)
            self.assertEqual(sent, [{'type': 'websocket.close'}])
        asyncio.run(run())
        self.assertEqual(metrics.registry.get_sample_value('ask_slt_http_last_request_timestamp_seconds'), 0)


if __name__ == '__main__':
    unittest.main()
