# Langfuse V4 migration status

Status date: 2026-11-07

## Workmate API repository

Implemented:

- Python SDK constrained to `langfuse>=4.9.0,<5.0.0`. This is above the
  `4.7.0` minimum for real-time V4 ingestion and avoids an accidental major
  upgrade during a production rebuild.
- Each `/api/v1/chat` execution creates one logical root observation named
  `workmate-chat` containing the application request input and final response
  output.
- `user_id`, `session_id`, tags, trace name, and environment propagate to the
  root and child LangChain observations. Request-scoped attributes use
  `propagate_attributes()`; the process environment is configured through
  `LANGFUSE_TRACING_ENVIRONMENT` in Compose.
- Feedback uses `create_score()` and the Workmate thread as a Langfuse session
  ID. It no longer incorrectly treats the thread ID as a trace ID.
- No direct deprecated `GET /api/public/observations`, `/metrics`, or `/traces`
  calls exist in this repository or its Git history.

The root observation filter for whole-request evaluators is:

- `Is Root Observation = true`
- observation name `workmate-chat`
- environment `production`

## WorkmateAI-LangfuseDailyReport

**Not present in this repository.** Its detected user agent and endpoint calls
belong to a separate deployment or codebase. Do not mark the report migration
complete until that repository is supplied and tested.

Required implementation in that repository:

1. Replace trace and observation reads with
   `GET /api/public/v2/observations`.
2. Bound every request with inclusive `fromStartTime` and exclusive
   `toStartTime` values.
3. Request only the necessary field groups. A report reconstructing requests,
   responses, latency, usage, and cost normally needs
   `core,basic,io,usage,metrics,trace_context`.
4. Follow the opaque `meta.cursor` until no cursor remains. Never recreate or
   numerically increment the cursor.
5. Group observation rows by `traceId`. Use `isRootObservation=true` for the
   logical application root; use `parentObservationId` only when reconstructing
   the physical tree.
6. Read overall Workmate request input/output from the `workmate-chat` root
   observation.
7. Replace the legacy metrics call with `GET /api/public/v2/metrics` and an
   observations-view query. Revalidate every count, token, latency, usage, and
   cost calculation because observation counts are not trace counts.
8. Set an explicit caller user agent, for example
   `WorkmateAI-LangfuseDailyReport/2.0`, to make future audits attributable.

Example observation paging contract (credentials deliberately omitted):

```text
GET /api/public/v2/observations
  ?fromStartTime=<ISO-8601 UTC>
  &toStartTime=<ISO-8601 UTC>
  &fields=core,basic,io,usage,metrics,trace_context
  &limit=1000
  &cursor=<opaque cursor from prior meta.cursor>
```

Do not invent a Metrics API v2 request by mechanically translating the old
query. Capture the old report's business definitions first, then implement and
compare the V2 observations-view aggregations against a fixed UTC interval.

## Unknown caller

**Not identified from this repository.** No deprecated read endpoint occurs in
the current tree or any reachable Git revision.

Use the Migration Assistant timestamps together with Langfuse/reverse-proxy
access logs to identify the source IP, user agent, project/public-key identity,
and deployment. Search scheduled jobs, CI/CD, notebooks, dashboards, and other
repositories for these literal paths:

```text
/api/public/observations
/api/public/metrics
/api/public/traces
```

Never log or share the Basic Authorization header or secret key. Once found,
give the caller an explicit user agent and apply the same V2 migration.

## Three evaluators

**Pending Langfuse administrator access.** Evaluator configuration is held in
the Langfuse instance, not this repository.

For each evaluator:

1. Duplicate it rather than editing the active legacy evaluator in place.
2. Change the target from trace to observation.
3. Start with `Is Root Observation = true`, name `workmate-chat`, and
   environment `production`.
4. Map old trace input/output to root `observation.input` and
   `observation.output`.
5. Validate against real new observations and confirm exactly one score per
   intended trace.
6. Compare results with the old evaluator over the same sample.
7. Deactivate the legacy evaluator only after the replacement is verified.

## Cutover gate

Do not switch the Langfuse deployment to `events_only` until all items below are
evidenced:

- Workmate production image contains Python SDK 4.9.x.
- New `workmate-chat` root observations appear in real time with input/output
  and propagated attributes.
- Session feedback scores are accepted.
- The external DailyReport uses only V2 read APIs and its calculations have
  been reconciled.
- The unknown caller is identified and no longer uses deprecated endpoints.
- All three observation-level evaluators produce the expected score count.
- The Migration Assistant and access logs show no remaining deprecated calls.

