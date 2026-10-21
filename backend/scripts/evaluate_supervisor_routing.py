"""Compare the frozen baseline and Decisions on identical labeled inputs.

Default validates the dataset without network calls. --live calls only routing
models; no specialist tools, databases, HR APIs, or saved user chats are invoked.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from domain.routing.evaluation import compare


def load_cases(path: Path, split: str, limit: int | None) -> tuple[dict, list[dict]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = data["cases"]
    ids = [c["id"] for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate dataset IDs")
    from domain.config.supervisor_routing import SPECIALIST_ROUTING_PROFILES
    for case in cases:
        if not case.get("query") or not case.get("language") or case.get("split") not in ("development", "holdout"):
            raise ValueError("case missing query/language/split")
        if case["expected_action"] not in ("delegate", "multi_delegate", "clarify", "direct", "out_of_scope", "department_overview"):
            raise ValueError("invalid expected action")
        agents = case["expected_agents"]
        if len(agents) != len(set(agents)) or set(agents) - SPECIALIST_ROUTING_PROFILES.keys():
            raise ValueError("invalid expected agents")
        if (case["expected_action"] == "delegate" and len(agents) != 1) or (case["expected_action"] == "multi_delegate" and len(agents) < 2):
            raise ValueError("inconsistent route label")
    selected = [c for c in cases if split == "all" or c["split"] == split]
    if limit:
        selected = selected[:limit]
    if not selected:
        raise ValueError("empty selection")
    return data, selected


async def run_live(cases: list[dict], repeats: int, include_plans: bool) -> list[dict]:
    from langchain_core.messages import HumanMessage
    from core.config import settings
    from domain.archetypes import supervisor_agent as supervisor
    records = []
    for repeat in range(repeats):
        for index, case in enumerate(cases):
            state = {"messages": [HumanMessage(content=x) for x in [*case.get("history", []), case["query"]]],
                     "agent_id": "supervisor", **case.get("context", {})}
            backends = ["legacy", "decisions"] if (index + repeat) % 2 == 0 else ["decisions", "legacy"]
            for backend in backends:
                started = perf_counter()
                row = {"case_id": case["id"], "repeat": repeat, "backend": backend}
                try:
                    route = supervisor.route_request_legacy if backend == "legacy" else supervisor.route_request_decisions
                    # No fallback: a provider error is counted as an error, not
                    # disguised as a successful candidate prediction.
                    row["result"] = await asyncio.wait_for(route(state), timeout=max(30, settings.SUPERVISOR_DECISIONS_TIMEOUT_SECONDS + 2))
                except Exception as exc:
                    row["error"] = type(exc).__name__
                    row["result"] = {}
                row["latency_ms"] = round((perf_counter() - started) * 1000, 2)
                if include_plans and row["result"].get("routing_action") in ("delegate", "multi_delegate"):
                    # Planning is optional and reported separately; never execute specialists.
                    planner_state = {**state, **row["result"]}
                    if backend == "decisions" and row["result"].get("routing_metadata", {}).get("backend") == "decisions":
                        plan = await supervisor.decompose_query(planner_state)
                        row["plan"] = plan
                records.append(row)
                print(f"{repeat + 1}/{repeats} {case['id']} {backend}: {row.get('error') or row['result'].get('routing_action')}", flush=True)
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=BACKEND / "evaluation/supervisor_routing_cases.json")
    parser.add_argument("--split", choices=("all", "development", "holdout"), default="all")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--live", action="store_true", help="Make billed provider calls on the synthetic labeled inputs")
    parser.add_argument("--include-plans", action="store_true", help="Also generate candidate task plans for manual coverage review; does not run tools")
    parser.add_argument("--predictions", type=Path, help="Score a previous run without provider calls")
    parser.add_argument("--output", type=Path, default=BACKEND / "evaluation/results/supervisor_routing.json")
    args = parser.parse_args()
    if args.repeats < 1 or args.limit is not None and args.limit < 1 or args.live and args.predictions:
        parser.error("use positive repeats/limit, and choose either --live or --predictions")
    dataset, cases = load_cases(args.dataset, args.split, args.limit)
    print(f"Validated {len(cases)} cases; label status: {dataset['label_status']}")
    if not args.live and not args.predictions:
        print("No API calls made. Use --live for paired evaluation or --predictions to rescore saved records.")
        return
    original = json.loads(args.predictions.read_text(encoding="utf-8")) if args.predictions else None
    dataset_hash = hashlib.sha256(args.dataset.read_bytes()).hexdigest()
    if original and original.get("dataset_sha256") != dataset_hash:
        parser.error("dataset differs from the recorded run; preserve the original labels when rescoring")
    records = asyncio.run(run_live(cases, args.repeats, args.include_plans)) if args.live else original["records"]
    selected_ids = {c["id"] for c in cases}
    records = [row for row in records if row["case_id"] in selected_ids]
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=BACKEND, capture_output=True, text=True).stdout.strip()
    from core.config import settings
    source_files = ["domain/archetypes/supervisor_agent.py", "domain/routing/decisions.py",
                    "domain/config/supervisor_routing.py", "domain/routing/evaluation.py"]
    code_hashes = {name: hashlib.sha256((BACKEND / name).read_bytes()).hexdigest() for name in source_files}
    report = {"created_at_utc": datetime.now(timezone.utc).isoformat(),
              "git_revision": revision, "source_sha256": code_hashes, "working_tree_may_include_changes": True,
              "dataset_sha256": hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
              "label_status": dataset["label_status"], "split": args.split,
              "configuration": {key: getattr(settings, key) for key in (
                  "ROUTING_EMBEDDING_PROVIDER", "ROUTING_EMBEDDING_MODEL", "SUPERVISOR_DECISIONS_MODEL",
                  "SUPERVISOR_DECISIONS_THRESHOLD", "SUPERVISOR_DECISIONS_UNCERTAIN_THRESHOLD",
                  "LLM_PROVIDER", "LLM_MODEL")},
              "records": records, "summary": compare(cases, records)}
    if original:
        # A rescore must not mislabel historical predictions with today's model,
        # thresholds or source hashes.
        report = {**original, "records": records, "summary": report["summary"],
                  "rescored_at_utc": report["created_at_utc"], "rescored_split": args.split}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: {k: v for k, v in value.items() if k not in ("by_language", "disagreements")}
                      for key, value in report["summary"].items() if isinstance(value, dict)}, indent=2))
    print(f"Report: {args.output}")


if __name__ == "__main__":
    main()
