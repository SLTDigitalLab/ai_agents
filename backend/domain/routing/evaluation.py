"""Paired routing metrics. Labels, never model confidence, define correctness."""
from __future__ import annotations

import math
import random
from statistics import mean


def selected_agents(result: dict) -> set[str]:
    if result.get("routing_action") not in ("delegate", "multi_delegate", "department_overview"):
        return set()
    return set(result.get("routed_agent_ids") or ([result["routed_agent_id"]] if result.get("routed_agent_id") else []))


def score_case(case: dict, record: dict) -> dict:
    result = record.get("result") or {}
    expected, actual = set(case["expected_agents"]), selected_agents(result)
    action_ok = not record.get("error") and result.get("routing_action") == case["expected_action"]
    return {"correct": bool(action_ok and actual == expected), "action_correct": bool(action_ok),
            "missing": sorted(expected - actual), "extra": sorted(actual - expected),
            "tp": len(actual & expected), "fp": len(actual - expected), "fn": len(expected - actual)}


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def _summary(cases: dict, rows: list[dict]) -> dict:
    scores = [score_case(cases[row["case_id"]], row) for row in rows]
    tp, fp, fn = (sum(x[key] for x in scores) for key in ("tp", "fp", "fn"))
    latencies = [row["latency_ms"] for row in rows]
    return {"n": len(rows), "exact_accuracy": mean(x["correct"] for x in scores) if scores else None,
            "action_accuracy": mean(x["action_correct"] for x in scores) if scores else None,
            "required_agent_recall": tp / (tp + fn) if tp + fn else None,
            "agent_precision": tp / (tp + fp) if tp + fp else None,
            "missed_agents": fn, "unnecessary_agents": fp,
            "errors": sum(bool(row.get("error")) for row in rows),
            "fallbacks": sum(bool(row.get("result", {}).get("routing_metadata", {}).get("fallback")) for row in rows),
            "p50_ms": percentile(latencies, .5), "p95_ms": percentile(latencies, .95)}


def compare(cases: list[dict], records: list[dict]) -> dict:
    by_id = {case["id"]: case for case in cases}
    if len(by_id) != len(cases):
        raise ValueError("duplicate case IDs")
    indexed = {}
    for row in records:
        key = (row["case_id"], row.get("repeat", 0), row["backend"])
        if row["case_id"] not in by_id or row["backend"] not in ("legacy", "decisions") or key in indexed:
            raise ValueError("unknown or duplicate prediction")
        indexed[key] = row
    pairs = sorted({(key[0], key[1]) for key in indexed})
    if not pairs or {pair[0] for pair in pairs} != set(by_id):
        raise ValueError("every case needs paired predictions")
    if any((case_id, repeat, backend) not in indexed for case_id, repeat in pairs for backend in ("legacy", "decisions")):
        raise ValueError("unpaired predictions")
    summaries = {}
    for backend in ("legacy", "decisions"):
        rows = [row for row in records if row["backend"] == backend]
        summaries[backend] = _summary(by_id, rows)
        summaries[backend]["by_language"] = {language: _summary(by_id, [r for r in rows if by_id[r["case_id"]]["language"] == language])
                                              for language in sorted({c["language"] for c in cases})}
        summaries[backend]["multi_department"] = _summary(by_id, [r for r in rows if len(by_id[r["case_id"]]["expected_agents"]) > 1])
    wins, losses, disagreements, per_case = 0, 0, [], {}
    for case_id, repeat in pairs:
        old = score_case(by_id[case_id], indexed[(case_id, repeat, "legacy")])
        new = score_case(by_id[case_id], indexed[(case_id, repeat, "decisions")])
        delta = int(new["correct"]) - int(old["correct"])
        wins += delta > 0
        losses += delta < 0
        per_case.setdefault(case_id, []).append(delta)
        if delta or old["missing"] != new["missing"] or old["extra"] != new["extra"]:
            disagreements.append({"case_id": case_id, "repeat": repeat, "legacy": old, "decisions": new})
    # Resample cases, not repeated calls: rephrasings still need independent SME
    # review; a synthetic set's CI is not a population-level accuracy guarantee.
    deltas = [mean(values) for values in per_case.values()]
    rng = random.Random(17)
    bootstrap = sorted(mean(rng.choices(deltas, k=len(deltas))) for _ in range(2000))
    summaries["paired"] = {"wins": wins, "losses": losses, "ties": len(pairs) - wins - losses,
                           "exact_accuracy_delta": mean(deltas),
                           "case_bootstrap_95_interval": [bootstrap[49], bootstrap[1949]],
                           "disagreements": disagreements}
    summaries["interpretation"] = "Routing-only results. Required-agent recall does not prove every subquestion was answered. Synthetic labels need department review; no production-improvement claim or automatic promotion."
    return summaries
