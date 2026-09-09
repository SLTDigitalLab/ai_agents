"""
Per-ticket detail run for the full candidate pipeline (Hybrid Retrieval ->
Candidate Reranker -> Top-k -> Hierarchical LLM Classifier -> Confidence
Check via self-consistency voting) — see domain.helpdesk.pipeline.
category_classification.classify_ticket_category_pipeline().

Like domain/helpdesk/scripts/run_prediction_detail.py (which does this for the
deployed classify_ticket_category_vector()), this keeps the full
per-ticket evidence — not just the final accuracy number — so the result
can be inspected candidate-by-candidate: what hybrid retrieval surfaced
(and from which source: kb / example / kb+example), what the hierarchical
classifier picked on each self-consistency pass, and the resulting
confidence score.

Must run inside the backend container/environment (same requirement as
run_accuracy_eval.py / run_prediction_detail.py).

Usage (from /app inside the backend container):
    python domain/helpdesk/scripts/run_pipeline_eval.py \
        --input domain/helpdesk/data/representative_eval_200.xlsx \
        --output domain/helpdesk/data/pipeline_eval_200.xlsx \
        --top-k 5 --self-consistency-n 3 --concurrency 5
"""

import argparse
import asyncio
import random
import sys

import pandas as pd

sys.path.insert(0, "/app")

from domain.helpdesk.pipeline.category_classification import classify_ticket_category_pipeline  # noqa: E402
from domain.helpdesk.tools.hybrid_retrieval import search_hybrid_candidates  # noqa: E402

MAX_ATTEMPTS = 4
BASE_DELAY_SECONDS = 2.0

# Below this self-consistency agreement fraction, a live caller should
# route to a clarification turn instead of creating the ticket outright
# (see the Confidence Check stage in the discussed pipeline). Recorded
# here for analysis, not enforced — this script always records the
# majority-vote pick as its "final" prediction, low-confidence or not, so
# the eval measures "what would ticket creation have used" alongside "how
# often would that have been gated to a clarification instead."
LOW_CONFIDENCE_THRESHOLD = 0.67


async def classify_row(
    sem: asyncio.Semaphore, idx: int, message: str, k: int, n: int
):
    async with sem:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                candidates = await search_hybrid_candidates(str(message), k=k)
                main_category, sub_category, confidence = await classify_ticket_category_pipeline(
                    str(message), k=k, self_consistency_n=n
                )
                return idx, candidates, main_category, sub_category, confidence
            except Exception as exc:
                if attempt == MAX_ATTEMPTS:
                    print(f"[row {idx}] ERROR after {MAX_ATTEMPTS} attempts: {exc}")
                    return idx, [], "", "", 0.0
                delay = BASE_DELAY_SECONDS * (2 ** (attempt - 1)) + random.uniform(0, 1)
                print(f"[row {idx}] attempt {attempt} failed ({exc}); retrying in {delay:.1f}s")
                await asyncio.sleep(delay)


async def run(args):
    full_df = pd.read_excel(args.input)
    required_cols = {"message", "true_category"}
    missing = required_cols - set(full_df.columns)
    if missing:
        raise SystemExit(f"Input file is missing expected column(s): {missing}")

    start_row = args.start_row
    end_row = args.end_row if args.end_row is not None else len(full_df)

    has_sub = "true_sub_category" in full_df.columns
    cols = ["message", "true_category"] + (["true_sub_category"] if has_sub else [])
    df = full_df.iloc[start_row:end_row][cols].reset_index(drop=True)
    if not has_sub:
        df["true_sub_category"] = ""

    if df.empty:
        raise SystemExit(f"No rows in range [{start_row}:{end_row}) — file has {len(full_df)} rows")

    k = args.top_k
    n = args.self_consistency_n
    print(
        f"Testing rows [{start_row}:{end_row}) -> {len(df)} rows, "
        f"top_k={k}, self_consistency_n={n} "
        f"({n * 2} LLM calls/ticket: {n} hierarchical passes x 2 stages each)"
    )

    sem = asyncio.Semaphore(args.concurrency)
    tasks = [
        classify_row(sem, idx, row["message"], k, n) for idx, row in df.iterrows()
    ]

    candidates_by_row = [[] for _ in range(len(df))]
    predicted_category = [""] * len(df)
    predicted_sub_category = [""] * len(df)
    confidence_by_row = [0.0] * len(df)

    done = 0
    for coro in asyncio.as_completed(tasks):
        idx, candidates, main_category, sub_category, confidence = await coro
        candidates_by_row[idx] = candidates
        predicted_category[idx] = main_category
        predicted_sub_category[idx] = sub_category
        confidence_by_row[idx] = confidence
        done += 1
        if done % 10 == 0 or done == len(df):
            print(f"classified {done}/{len(df)}")

    for i in range(k):
        df[f"cand{i+1}_category"] = [
            (c[i].get("main_category", "") if len(c) > i else "") for c in candidates_by_row
        ]
        df[f"cand{i+1}_subcategory"] = [
            (c[i].get("sub_category", "") if len(c) > i else "") for c in candidates_by_row
        ]
        df[f"cand{i+1}_source"] = [
            (c[i].get("source", "") if len(c) > i else "") for c in candidates_by_row
        ]
        df[f"cand{i+1}_score"] = [
            (round(float(c[i].get("_score", 0.0)), 4) if len(c) > i else None)
            for c in candidates_by_row
        ]

    df["predicted_category"] = predicted_category
    df["predicted_sub_category"] = predicted_sub_category
    df["confidence"] = confidence_by_row
    df["low_confidence"] = df["confidence"] < LOW_CONFIDENCE_THRESHOLD

    true_norm = df["true_category"].astype(str).str.strip().str.replace(r"\s+", " ", regex=True)
    pred_norm = df["predicted_category"].astype(str).str.strip().str.replace(r"\s+", " ", regex=True)
    df["correct"] = true_norm == pred_norm

    def _true_in_candidates(row):
        true_cat = str(row["true_category"]).strip()
        cand_cats = [str(row.get(f"cand{i+1}_category", "")).strip() for i in range(k)]
        return true_cat in cand_cats

    df["true_category_in_top_k"] = df.apply(_true_in_candidates, axis=1)

    overall_accuracy = df["correct"].mean() * 100
    recall_at_k = df["true_category_in_top_k"].mean() * 100
    low_conf_rate = df["low_confidence"].mean() * 100
    # Accuracy specifically among the high-confidence subset — the number
    # that would actually reach ticket creation if low-confidence tickets
    # were gated to a clarification turn instead.
    high_conf_df = df[~df["low_confidence"]]
    high_conf_accuracy = high_conf_df["correct"].mean() * 100 if len(high_conf_df) else float("nan")

    print(f"\nFinal (LLM-chosen) category accuracy: {overall_accuracy:.1f}% ({df['correct'].sum()}/{len(df)})")
    print(f"Recall@{k} (true category anywhere in retrieved candidates): {recall_at_k:.1f}%")
    print(f"Low-confidence rate (<{LOW_CONFIDENCE_THRESHOLD:.0%} self-consistency agreement): {low_conf_rate:.1f}%")
    print(
        f"Accuracy on HIGH-confidence subset only ({len(high_conf_df)}/{len(df)} tickets, "
        f"i.e. what ticket-creation would see if low-confidence tickets were "
        f"clarified instead): {high_conf_accuracy:.1f}%"
    )

    with pd.ExcelWriter(args.output) as writer:
        df.to_excel(writer, sheet_name="results", index=False)

    print(f"\nWrote per-ticket detail to {args.output}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="domain/helpdesk/data/representative_eval_200.xlsx")
    parser.add_argument("--output", default="domain/helpdesk/data/pipeline_eval_200.xlsx")
    parser.add_argument("--start-row", type=int, default=0)
    parser.add_argument("--end-row", type=int, default=None)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--self-consistency-n", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=5)
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
