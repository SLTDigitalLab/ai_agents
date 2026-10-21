# Supervisor routing: implementation and evidence

The OpenAI Decisions candidate is implemented behind `SUPERVISOR_ROUTING_BACKEND`.
The default remains `legacy`. A passing unit test or a higher model probability
is **not** evidence that real users receive better answers.

## What changed

- `legacy` runs the original embedding, keyword and threshold decisions.
- `decisions` asks 14 independent predicates in one `POST /v1/decisions` call:
  department relevance for all ten specialists, insufficient information, whole
  request outside scope, general help, and unsupported parts. A single request
  may select three or more specialists. Uncertain ownership asks for clarification.
- The candidate uses masked current/recent user text and prior department IDs.
  It never sends authenticated identity or assistant/tool answers to the router.
- After selection, a structured generative planner creates focused tasks. Tasks
  have IDs and explicit prerequisites. Independent tasks run with bounded
  concurrency; prerequisites run first. Two questions for one specialist remain
  two tracked tasks.
- An invalid plan (missing specialist, duplicate task IDs, unknown prerequisite,
  or dependency cycle) runs no specialist and asks the user to clarify. This is
  reported as a planning error rather than disguised as a successful answer.
- Answers are assembled in task order with their citations and exact numeric
  content preserved. Each unavailable, dependent-on-failure or unassigned part
  produces a visible notice. Routing probabilities never rank answer quality.
- Provider errors/refusals/missing answers fall back to the baseline, with a
  fallback flag. If both routing services fail, the graph asks for clarification.

No changes are required to the frontend, HTTP chat contract, retrieval embeddings,
Qdrant collections, authentication or guardrail policy. `httpx` is already a
dependency; the native HTTP contract avoids an OpenAI SDK/LangChain upgrade.

Official contract: <https://developers.openai.com/api/docs/guides/decisions> and
<https://developers.openai.com/api/reference/resources/decisions/methods/create>.
The endpoint is currently beta; account access must be checked in the live run.

## Start when there are no known failure examples

`backend/evaluation/supervisor_routing_cases.json` contains 54 synthetic draft
labels: 28 development and 26 holdout requests. It covers all ten departments,
two-to-five-department requests, multiple questions for the same department,
incidental/negated mentions, substantive questions after thanks, follow-ups,
topic changes, vague and unsupported requests, and English/Sinhala/Tamil/mixed
language inputs. These are based on the repository's department definitions,
not labels from production or independently adjudicated by department experts.

Use this as a reproducible engineering baseline. Have a representative of each
department review their cases and ambiguous boundaries before interpreting
accuracy as business correctness. Add anonymized real queries later and label
them without showing reviewers which router produced which answer. Keep
rephrasings and turns from one conversation in the same split. Do not use the
holdout set to tune thresholds or prompts; obtain a fresh holdout if you do.

## Run the comparison

From the repository root on Windows:

```powershell
# Validate the labels and case selection. No network calls.
venv/Scripts/python.exe backend/scripts/evaluate_supervisor_routing.py

# Paired routing-only run. Makes billed API/embedding calls on the synthetic set.
# Does not invoke specialists, HR APIs, retrieval, or saved conversations.
venv/Scripts/python.exe backend/scripts/evaluate_supervisor_routing.py --live --split development --repeats 3 --output backend/evaluation/results/development.json

# After development settings are fixed, evaluate the reserved holdout.
venv/Scripts/python.exe backend/scripts/evaluate_supervisor_routing.py --live --split holdout --repeats 3 --output backend/evaluation/results/holdout.json

# Optionally generate candidate task plans for blinded coverage review.
# No specialist tools execute, but generative planning incurs additional cost.
venv/Scripts/python.exe backend/scripts/evaluate_supervisor_routing.py --live --split holdout --include-plans --output backend/evaluation/results/holdout-plans.json

# Recompute metrics from an existing complete report without API calls.
venv/Scripts/python.exe backend/scripts/evaluate_supervisor_routing.py --predictions backend/evaluation/results/holdout.json --split holdout --output backend/evaluation/results/rescored.json

# Mock provider contract + routing, compiled-graph, failure and scoring tests.
venv/Scripts/python.exe -m pytest backend/tests/test_supervisor_decisions.py -q
```

The evaluator alternates execution order, sends both routers identical state,
and bypasses operational fallback so failed candidate calls count as failures.
It records the dataset hash, Git revision, model/policy settings, returned model,
prompt hash, token usage where reported and individual predictions. First-call
initialization is included: compare individual records and repeat runs to assess
warm latency. Reports are ignored by Git and can contain synthetic query plans;
handle future reports containing real queries according to the project's policy.

## How to read the evidence

| Measure | What it establishes |
|---|---|
| Exact routing accuracy | Action and complete specialist set both match the human label |
| Required-agent recall / missed agents | Whether a required department was omitted, including the third/fourth/fifth |
| Agent precision / unnecessary agents | Whether routing triggers irrelevant work |
| Action accuracy | Correct clarification, direct answer, decline or delegation action |
| Per-language and multi-department slices | Whether a global average hides a regression |
| Paired wins/losses | Requests improved versus requests regressed on exactly the same inputs |
| Case-bootstrap interval | Sensitivity of the measured difference to this case sample; not a production guarantee |
| p50/p95 routing latency | Router wall-clock time, including failures; not answer latency |
| Errors / fallback flags | Endpoint availability and operational reliance on the baseline |

The evaluator does **not** measure factual answer quality, retrieval quality,
end-to-end cost, or whether the planner captured every semantic part of a user
request. A required-agent label is not a complete question-level coverage label.
Stored task statuses account for planned tasks, not proof of correctness.
The current answer availability check uses the existing decline detector, so
unusual or multilingual decline phrasing also needs manual review.

For a full system comparison, use identical document snapshots and authenticated
synthetic test accounts, run both complete graphs, and have blinded reviewers
check every requested subquestion, correctness, source support, duplicate or
contradictory answers, and failure notices. Include a multi-step question whose
second answer requires a fact from the first. Measure time to first visible
answer and completion latency plus all model, embedding and retrieval costs.
The candidate adds a planning call for specialist requests, so faster routing
alone does not imply a faster complete chat.

## Promotion criteria and rollback

Agree on criteria before examining the holdout. A suggested starting gate is:

1. Higher exact accuracy and fewer missed departments on a department-reviewed
   holdout; examine every paired regression rather than just the average.
2. Improved multi-department recall without increasing irrelevant calls.
3. No material supported-language regression; expand the small language slices
   before making a language-specific claim.
4. No silent dropped parts in the reviewed end-to-end examples. A failed
   prerequisite must be surfaced, not used as fabricated evidence.
5. Acceptable measured end-to-end p95 latency, request cost, and failure rate.

Synthetic draft results alone cannot pass the business-quality gate. There is
no automatic promotion based on this small set or on model confidence.

## Measured results: 2026-10-15

The final policy (`workmate-departments-v3`, relevance threshold 0.80) was fixed
after development testing. Both routers were then run twice on the 26 reserved
holdout cases (52 predictions per router). The repeated calls are not 52
independent user scenarios. No holdout-driven prompt or threshold change was made.

| Holdout routing measure | Original | Decisions |
|---|---:|---:|
| Exact routing match | 61.5% (16/26 cases, both repeats) | 96.2% (25/26 cases, both repeats) |
| Required-agent recall | 74.2% | 100% |
| Agent precision | 74.2% | 96.9% |
| Exact match on multi-department cases | 40% (2/5) | 100% (5/5) |
| Missed required agents across both repeats | 16 | 0 |
| Unnecessary agents across both repeats | 16 | 2 |
| Median routing latency | 0.287 s | 0.788 s |
| p95 routing latency | 1.288 s | 2.762 s |
| Provider errors in this run | 0 | 0 |

The candidate improved nine unique holdout cases and regressed none relative to
the original router; both failed one ambiguous follow-up. After a question about
staff loan applications and office vehicle booking, "Which form do I need for
that?" was labeled as needing clarification. The candidate instead selected HR.
This failure remains visible in the report; confidence is not proof of correctness.

On the 28 development cases, two repeats yielded 50% exact routing accuracy for
the original and 100% for the revised candidate. These are development results,
not an additional independent holdout. Four live candidate plans (one to four
departments) were also inspected; all contained the requested focused questions
and passed structural validation. No specialist tools were run in these live
checks. Compiled-graph execution, dependent failures, multi-specialist coverage,
and citation preservation were checked with deterministic specialist responses.
The final backend regression run passed 72 tests and 101 parameterized subtests.

Reports, with individual predictions, token usage, source/dataset hashes and
per-language slices:

- `backend/evaluation/results/development-final.json`
- `backend/evaluation/results/holdout.json`
- `backend/evaluation/results/planning.json`

The dataset is small and synthetic, labels still need department review, and the
candidate's router was slower. There is no measured end-to-end answer-quality,
latency or cost improvement yet. Keep `legacy` as the default while reviewing
real queries and evaluating complete answers. This report supports a supervised
pilot, not a production-quality guarantee.

Configuration (root `.env`, then restart the backend):

```dotenv
SUPERVISOR_ROUTING_BACKEND=legacy
# legacy | shadow | decisions
SUPERVISOR_DECISIONS_MODEL=gpt-6-luna
# SUPERVISOR_DECISIONS_API_KEY=...  # otherwise OPENAI_API_KEY
SUPERVISOR_DECISIONS_TIMEOUT_SECONDS=8
SUPERVISOR_DECISIONS_THRESHOLD=0.80
SUPERVISOR_DECISIONS_UNCERTAIN_THRESHOLD=0.40
SUPERVISOR_MAX_CONCURRENCY=3
SUPERVISOR_ROUTING_LOG_TEXT=false
```

The probability cutoffs are provisional, not calibrated production thresholds.
Tune on development labels, including the cost of missed and unnecessary agents.
`shadow` serves the baseline result and records candidate action/agents in
`routing_metadata.shadow` and comparison logs; it does not execute candidate
specialists. It adds a provider call and can add latency, and disagreement alone
is not an accuracy label. After offline validation, shadow on a limited test
environment before a candidate canary. Roll back by setting the backend to
`legacy` and restarting; the compiled graph reads the mode per turn.

Generated task plans are limited to 20 tasks and execution concurrency to ten
at most (three by default). An oversized or invalid plan produces a visible
clarification rather than truncating the user's questions. A missing provider
key never silently enables the candidate.

## Viewing supervisor logs

The backend already configures INFO logging. Supervisor traces use one JSON
object per line with the prefix `Supervisor trace |`. Follow them with:

```powershell
docker compose logs -f --tail 100 backend | Select-String 'Supervisor'
```

When running Uvicorn directly, the same events appear in its terminal. Restart
the backend to load code changes if it is not running with automatic reload.
For the development Compose file, the source is mounted; `docker compose restart
backend` reloads code. If changing `.env` values passed through Compose, recreate
the service with `docker compose up -d --force-recreate backend` instead.

Every routed turn has a unique `trace_id`. All its planning and execution events
share that value. `conversation_id` is a stable hash of the internal thread ID,
so concurrent conversations can be separated without logging the raw identity.

| Event | Fields to inspect |
|---|---|
| `routing_started` | Selected mode: legacy, shadow or decisions |
| `routing_decided` | Action, reason, selected departments, probabilities or legacy similarity scores, model, thresholds, routing duration and fallback metadata |
| `routing_comparison` | Baseline and candidate routing in shadow mode; only the baseline executes |
| `routing_fallback` | Candidate failure type before falling back |
| `planning_started` / `planning_completed` | Planning model/time, task IDs, specialist assignments, dependencies and unassigned-part count |
| `planning_failed` | Error type and planning duration |
| `specialist_started` / `specialist_completed` | Specialist, duration, returned/unavailable/error and response length |
| `task_started` / `task_completed` | Task ID, specialist, prerequisites, duration and answered/unavailable/dependency_failed status |
| `execution_completed` | Overall task-execution duration and all task statuses |
| `synthesis_evaluated` | Legacy specialists whose responses passed or failed the existing usable-answer check |
| `synthesis_completed` | Assembly duration, planned/answered counts, unresolved count and final response length |

Task/plan events apply to the Decisions path. The original router also has
existing diagnostic lines, with the new correlated specialist events added.
The default routing mode is still `legacy`; logging does not enable Decisions.

To test the candidate locally, set `SUPERVISOR_ROUTING_BACKEND=decisions` in the
root `.env` and restart the backend. Confirm `routing_started.mode` is `decisions`
and `routing_decided.metadata.fallback` is false. Legacy similarity scores are
not Decisions probabilities.

Both decomposition schemas explicitly forbid extra object properties; all
fields, including empty dependency and unassigned-question lists, are required.
This also covers Responses API streaming, where the installed LangChain adapter
sends the generated schema directly rather than using SDK parse normalization.
A transport-level streaming regression verifies both planners' outgoing schemas
and parsed results.

### Local runtime check, 2026-10-16

The reported query was: `procedure to follow when an equipment lost under my
custody`. The supplied application trace used `legacy`, selected Admin and HR,
and hit a schema rejection during streamed decomposition. Both specialists then
received the original question. Their 40-character responses alone do not prove
that either returned a useful answer.

After the schema fix, a live streamed graph containing only the decomposition
node succeeded for both schemas. Legacy decomposition retained Admin. The
Decisions planner schema was checked with explicitly supplied Admin/HR choices;
that forced planner test is not an automatic routing success. No specialists or
business tools were executed in these smoke checks.

The independent live Decisions classification requested clarification instead of
delegating: Admin 0.20, HR 0.04, IT 0.03, all below the 0.80 selection threshold.
This is a real-query limitation beyond the synthetic benchmark. Confirm the
department that owns lost-equipment procedures against the relevant policy,
then include the reviewed example in development evaluation before changing
scope descriptions or thresholds. The schema repair does not change routing
prompts, thresholds or the default mode. Backend regression validation after
the repair: 75 tests and 101 subtests passed.

Additional query previews are off by default. Set `SUPERVISOR_ROUTING_LOG_TEXT=true`
to include masked, length-limited query/subquestion previews in the new trace
events. This masks common identifiers, not every kind of sensitive information.
Answer bodies, tool results, credentials and HR API responses are not included
in these events. Existing baseline diagnostics can still include query snippets;
the switch governs the new structured trace previews. The previous raw specialist
answer dump has been removed.

These durations measure different stages: routing includes its provider round
trip; planning includes the generative call; execution includes dependency waves
and concurrency limits; synthesis here is deterministic assembly. Task durations
overlap when tasks run concurrently, so adding them does not give total latency.
