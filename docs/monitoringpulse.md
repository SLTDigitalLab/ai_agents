# MonitoringPulse HTTP telemetry

Implementation status: implemented in the repository, not yet enabled or verified
against production Prometheus. No production connectivity or live traffic claims
are made here.

## Scope and request path

Browser HTTPS requests to `https://aiagents.sltdigitallab.lk/api/v1/...` pass
through host Nginx to `127.0.0.1:8100`, then `slt_backend` (Compose service
`backend`, port 8000). Frontend service `frontend`, container `slt_frontend`,
uses container port 3000 and host binding `127.0.0.1:3100`.
The private Docker address is `backend:8000` only on a shared network.

Only HTTP requests under `/api/v1/` are measured. `/metrics`, root, API docs,
and `/api/v1/health`, `/api/v1/healthz`, `/api/v1/ready`, `/api/v1/readyz`
(including trailing slashes) are excluded. This does not create health or readiness
routes. If new health routes are added, extend the exclusion list.

## Enablement and security

Disabled by default (`METRICS_ENABLED` unset/false). To enable, configure
`METRICS_ENABLED=true` and a dedicated `METRICS_BEARER_TOKEN` with at least
32 ASCII characters, no whitespace, generated securely. Store it in the server's
protected environment file and the monitoring system's protected secret file.
Never commit it or send it in chat. Do not reuse a user login token or provider key.

The backend serves GET `/metrics` with bearer authentication, no-store caching,
and no OpenAPI listing. Missing/wrong credentials return 401. Invalid enabled
configuration fails startup. Existing API authentication is unchanged.

The backend scrape endpoint is `/metrics`, NOT `/api/v1/metrics`. Backend port 8100
remains bound to host loopback and no new host port is published. For the hosted
MonitoringPulse server, Nginx exposes only the exact TLS path
`https://aiagents.sltdigitallab.lk/internal/metrics`, allowlisted to source IP
`124.43.145.146/32`, and proxies it to backend `/metrics`. Nginx continues to deny
the native `/metrics`, `/api/metrics`, and all corresponding subpaths. Both the
source-IP restriction and dedicated bearer token are required. If the monitoring
egress IP changes, update the allowlist before scraping. Shared-network peers can
reach backend port 8000, so do not join untrusted containers to that network.

Use exactly one Uvicorn worker, as in the existing production Compose command.
The code rejects configured WEB_CONCURRENCY other than 1 and multiprocess mode;
do not add CLI `--workers` greater than 1. Multi-worker aggregation is not implemented.
Counters reset on restart; use Prometheus `rate`/`increase`, not raw differences
across restarts. No process/platform collectors are registered.

## Metrics contract

| Metric | Type / units | Exported labels |
| --- | --- | --- |
| `ask_slt_http_requests_total` | counter, requests | method, route, status_code |
| `ask_slt_http_request_duration_seconds` | histogram, seconds | method, route; bucket series also le |
| `ask_slt_http_requests_in_progress` | gauge, requests | method |
| `ask_slt_http_last_request_timestamp_seconds` | gauge, Unix seconds | none |
| `ask_slt_http_terminations_total` | counter, transport terminations | method, route, outcome |

The client also exports counter/histogram `_created` timestamps. Duration exports
`_bucket`, `_sum`, `_count`; buckets are .01, .05, .1, .25, .5, 1, 2.5, 5, 10,
30, 60, 120, 300, 900 and +Inf seconds. Configure fixed application, environment,
service labels on the Prometheus target (example below).

Routes are matched templates, e.g. `/api/v1/chat/{agent_id}/{thread_id}`;
unmatched paths are grouped as `unmatched`. Unknown HTTP methods use `OTHER`.
Raw URLs, queries, IDs, bodies, headers and personal data are never labels.
In-progress requests use only method because the route is not yet known at entry.

Requests are counted once at final body delivery or abnormal termination.
Statuses 200–299 are HTTP success, 400–599 HTTP failures, 300–399 redirects.
`status_code="none"` means no response status was sent before termination; it is
not an HTTP success or an invented HTTP status. An unhandled exception before
response start is counted as 500, matching Starlette's outer error handler.
Latency ends on the final response body, excluding subsequent background work,
or on abnormal termination. Last observed timestamp updates at request arrival,
and is zero until the first observed API request.

Transport outcomes: `completed`, `exception`, `cancelled`, `disconnected`,
`incomplete`. An interrupted stream can retain HTTP 200 but also record
`outcome="exception"`. A model error encoded normally inside a completed response
is NOT detected by these metrics. No response content is inspected. Client/network
failures are only visible where ASGI reports them. Nginx-generated 408/504 and
requests never reaching the app require separate proxy telemetry. Application
408/504 responses are included in HTTP failures.

WebSocket/voice connections are passed through unchanged and not counted as HTTP
requests. HTTP voice setup endpoints ARE counted. Direct browser-to-provider voice,
background-job outcomes and semantic AI success need separate future instrumentation.

## Attribution

These metrics identify API traffic received by this service, not proven frontend
origin. Internal calls and direct external API calls are included. No request-ID or
trace-context propagation is added in this change. Browser-supplied headers would
not prove origin. Request IDs, if added later for correlation, must not be metric labels.

## Prometheus configuration for hosted MonitoringPulse

```yaml
scrape_configs:
  - job_name: ask-slt-backend
    scrape_interval: 15s
    scrape_timeout: 10s
    metrics_path: /internal/metrics
    scheme: https
    authorization:
      type: Bearer
      credentials_file: /run/secrets/ask_slt_metrics_token
    static_configs:
      - targets: ["aiagents.sltdigitallab.lk:443"]
        labels:
          application: ask_slt
          environment: production
          service: backend
```

The secret file contains only the token and must be mounted read-only into
Prometheus. Use the normal verified public certificate; do not disable certificate
verification. The Nginx allowlist assumes Prometheus reaches the application from
`124.43.145.146`. Confirm that egress address from the hosted monitoring server.
`localhost` in Prometheus is not the application host.

Example PromQL (for the selected time window):

```promql
sum(increase(ask_slt_http_requests_total{job="ask-slt-backend"}[5m]))
sum(increase(ask_slt_http_requests_total{job="ask-slt-backend",status_code=~"2.."}[5m]))
sum(increase(ask_slt_http_requests_total{job="ask-slt-backend",status_code=~"[45].."}[5m]))
histogram_quantile(0.95, sum by (le) (rate(ask_slt_http_request_duration_seconds_bucket{job="ask-slt-backend"}[5m])))
```

Do not add termination failures to HTTP failure totals without considering overlap.
An exception can already have a 500 status. Scrape `up` measures scrape health only.

## Verification and deployment checklist

Local isolated tests (no AI credits, database or real credentials required):

```powershell
./venv/Scripts/python.exe -m unittest discover -s backend/tests -p test_telemetry.py -v
```

Tests send synthetic GET `/api/v1/items/private-id` -> 200 and GET
`/api/v1/protected` -> 401. Each increments the corresponding counter and histogram
count by one, advances the arrival timestamp, and leaves in-progress at zero.
They also cover 500, unknown route normalization, scrape authentication, privacy,
disabled/invalid configuration, partial streams, cancellations, and WebSocket passthrough.
Synthetic routes are fixtures only and are not installed in production.

Sanitized representative test output (not a production scrape):

```text
# TYPE ask_slt_http_requests_total counter
ask_slt_http_requests_total{method="GET",route="/api/v1/items/{item_id}",status_code="200"} 1.0
ask_slt_http_requests_total{method="GET",route="/api/v1/protected",status_code="401"} 1.0
# TYPE ask_slt_http_requests_in_progress gauge
ask_slt_http_requests_in_progress{method="GET"} 0.0
```

Before deployment:
1. Transfer/commit ONLY telemetry changes onto the intended production branch.
   The development checkout also contains unrelated unfinished routing work.
2. Verify active Compose/Nginx paths and backend/Prometheus Docker networks. Back
   up the active proxy configuration and retain the previous backend image.
3. Build the backend image with the pinned prometheus-client dependency. Check
   available disk space before building; do not delete volumes to free space.
4. Apply the restricted `/internal/metrics` Nginx location and the public native-path
   denies, validate with `nginx -t`, and reload Nginx. Confirm the monitoring source
   address is still `124.43.145.146` first.
5. Set the protected telemetry environment variables and recreate ONLY the backend
   service using the production Compose file. A restart alone does not apply changed
   container environment variables. Keep one worker. No frontend build is needed.
6. Confirm startup and existing API authentication, then provision restricted scrape
   connectivity and the Prometheus secret/configuration. Validate/reload Prometheus.
7. Capture a baseline scrape, issue one authorized read-only API request through the
   browser and one unauthenticated protected GET expecting 401, then compare metrics.
   Account for concurrent production traffic; the matching series should increase
   by at least one. Do not trigger an intentional 500 on production or publish tokens.
8. Confirm public `/metrics` is denied, `/internal/metrics` is denied from other
   source IPs, an unauthenticated request from MonitoringPulse receives 401, and an
   authenticated scrape succeeds with target `up=1`. Verify real API series and
   timestamps separately from target health.

If needed, disable METRICS_ENABLED and recreate the backend; retain public denies.
Actual server deployment and successful Prometheus reachability remain pending.

Reference: https://prometheus.github.io/client_python/instrumenting/histogram/
