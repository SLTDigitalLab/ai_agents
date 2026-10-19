"""Opt-in, single-process HTTP telemetry. Never label metrics with request data."""
import asyncio
import os
import secrets
import time

from fastapi import HTTPException, Request
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from prometheus_client.exposition import CONTENT_TYPE_LATEST
from starlette.responses import Response

METHODS = frozenset({'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS', 'TRACE', 'CONNECT'})
EXCLUDED = frozenset({'/api/v1/health', '/api/v1/healthz', '/api/v1/ready', '/api/v1/readyz'})


class HttpMetrics:
    def __init__(self):
        self.registry = CollectorRegistry()
        self.requests = Counter('ask_slt_http_requests_total', 'HTTP responses by status; not AI outcomes.',
                                ['method', 'route', 'status_code'], registry=self.registry)
        self.duration = Histogram('ask_slt_http_request_duration_seconds',
                                  'Time until final response body or abnormal termination.',
                                  ['method', 'route'], registry=self.registry,
                                  buckets=(.01, .05, .1, .25, .5, 1, 2.5, 5, 10, 30, 60, 120, 300, 900))
        # Route is unknown at entry; deliberately do not use the raw path here.
        self.active = Gauge('ask_slt_http_requests_in_progress', 'Active API HTTP responses.',
                            ['method'], registry=self.registry)
        self.last = Gauge('ask_slt_http_last_request_timestamp_seconds',
                          'Unix timestamp of latest observed API request arrival; zero before traffic.',
                          registry=self.registry)
        self.terminations = Counter('ask_slt_http_terminations_total',
                                    'HTTP transport outcomes, not application or model outcomes.',
                                    ['method', 'route', 'outcome'], registry=self.registry)


class HttpMetricsMiddleware:
    def __init__(self, app, metrics):
        self.app = app
        self.metrics = metrics

    async def __call__(self, scope, receive, send):
        path = scope.get('path', '')
        if scope['type'] != 'http' or not path.startswith('/api/v1/') or path.rstrip('/') in EXCLUDED:
            return await self.app(scope, receive, send)
        method = scope['method'] if scope['method'] in METHODS else 'OTHER'
        started = time.perf_counter()
        status = None
        finished = False
        disconnected = False
        active = self.metrics.active.labels(method)
        active.inc()
        self.metrics.last.set(time.time())

        def finish(outcome):
            nonlocal finished
            if finished:
                return
            finished = True
            # FastAPI/Starlette attaches the matched route after routing. Never
            # fall back to scope['path'], which may contain IDs or personal data.
            route = getattr(scope.get('route'), 'path', None) or 'unmatched'
            self.metrics.requests.labels(method, route, str(status) if status is not None else 'none').inc()
            self.metrics.duration.labels(method, route).observe(time.perf_counter() - started)
            self.metrics.terminations.labels(method, route, outcome).inc()
            active.dec()

        async def observed_receive():
            nonlocal disconnected
            message = await receive()
            if message['type'] == 'http.disconnect':
                disconnected = True
            return message

        async def observed_send(message):
            nonlocal status
            await send(message)
            if message['type'] == 'http.response.start':
                status = message['status']
            elif message['type'] == 'http.response.body' and not message.get('more_body', False):
                finish('completed')

        try:
            await self.app(scope, observed_receive, observed_send)
        except asyncio.CancelledError:
            finish('cancelled')
            raise
        except Exception:
            # Starlette's outer ServerErrorMiddleware generates 500 if no
            # response has started. Preserve an already-sent streaming status.
            if status is None:
                status = 500
            finish('exception')
            raise
        finally:
            finish('disconnected' if disconnected else 'incomplete')


def install_http_metrics(app):
    """Disabled by default; fail closed on invalid enabled configuration."""
    if os.getenv('METRICS_ENABLED', '').lower() not in {'true', '1', 'yes'}:
        return
    token = os.getenv('METRICS_BEARER_TOKEN', '')
    if len(token) < 32 or not token.isascii() or any(c.isspace() for c in token):
        raise RuntimeError('METRICS_BEARER_TOKEN must be at least 32 ASCII characters without whitespace')
    if os.getenv('PROMETHEUS_MULTIPROC_DIR') or int(os.getenv('WEB_CONCURRENCY', '1')) != 1:
        raise RuntimeError('HTTP telemetry currently requires one backend worker')
    metrics = HttpMetrics()
    app.state.http_metrics = metrics
    app.add_middleware(HttpMetricsMiddleware, metrics=metrics)

    @app.get('/metrics', include_in_schema=False)
    async def scrape(request: Request):
        supplied = request.headers.get('authorization', '').encode('utf-8')
        expected = ('Bearer ' + token).encode('ascii')
        if not secrets.compare_digest(supplied, expected):
            raise HTTPException(status_code=401, detail='Not authenticated',
                                headers={'WWW-Authenticate': 'Bearer'})
        return Response(generate_latest(metrics.registry),
                        headers={'Content-Type': CONTENT_TYPE_LATEST, 'Cache-Control': 'no-store'})
