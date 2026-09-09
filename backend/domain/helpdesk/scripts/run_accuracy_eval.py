"""
Run the helpdesk agent's real category classifier (domain.helpdesk.pipeline.
category_classification.classify_ticket_category) over a slice of the
actual ticket data (DATA_Set.xlsx) and report accuracy.

Reads only the `message` and `Type` columns directly from the real file —
no separate sampling step. start_row/end_row select which slice of the
28,977 rows to test, so different ranges can be checked as separate runs
(e.g. rows 0-500, then 500-1000) without overlap.

Must run inside the backend container/environment — it imports the agent's
LLM bindings and hits the live Postgres category table, exactly like
production does.

Column names are configurable so a different data file (with different
headers) can be tested without editing the script — see --message-col,
--category-col, --subcategory-col. --subcategory-col is optional: some
files (e.g. the original DATA_Set.xlsx) only have a main category, while
others (e.g. Incidents_Feb.xlsx) also carry a ground-truth sub-category
(INC_SUB_TYPE_CD). When it's omitted, sub-category accuracy is simply
skipped rather than erroring.

Usage (from /app inside the backend container):
    python domain/helpdesk/scripts/run_accuracy_eval.py \
        --input domain/helpdesk/data/DATA_Set.xlsx \
        --output domain/helpdesk/data/eval_results_0_500.xlsx \
        --message-col message --category-col Type \
        --start-row 0 --end-row 500 \
        [--concurrency 5]

    # File with a ground-truth sub-category column too:
    python domain/helpdesk/scripts/run_accuracy_eval.py \
        --input domain/helpdesk/data/Incidents_Feb.xlsx \
        --output domain/helpdesk/data/eval_results_incidents.xlsx \
        --message-col message --category-col INCIDENT_TYPE_CD \
        --subcategory-col INC_SUB_TYPE_CD \
        --start-row 0 --end-row 500

    # Evaluate the vector-retrieval classifier instead of the default
    # prompt-based one (run domain/helpdesk/scripts/ingest_category_kb.py first):
    python domain/helpdesk/scripts/run_accuracy_eval.py --method vector \
        --input domain/helpdesk/data/Incidents_Feb.xlsx \
        --output domain/helpdesk/data/eval_results_vector.xlsx \
        --category-col INCIDENT_TYPE_CD --subcategory-col INC_SUB_TYPE_CD \
        --start-row 0 --end-row 500

    # Evaluate a fine-tuned model (see domain/helpdesk/scripts/
    # prepare_finetune_data.py + run_finetune_job.py):
    python domain/helpdesk/scripts/run_accuracy_eval.py --method finetuned \
        --model ft:gpt-4o-mini-2024-07-18:... \
        --input domain/helpdesk/data/finetune_test.xlsx \
        --output domain/helpdesk/data/eval_results_finetuned.xlsx \
        --category-col true_category --subcategory-col true_sub_category \
        --start-row 0 --end-row 500
"""

import argparse
import asyncio
import random
import sys

import pandas as pd

sys.path.insert(0, "/app")

from domain.helpdesk.pipeline.category_classification import (  # noqa: E402
    classify_ticket_category,
    classify_ticket_category_vector,
    classify_ticket_category_finetuned,
    classify_ticket_category_finetuned_vector,
    classify_ticket_category_examples,
    classify_ticket_category_pipeline,
)

# Concurrent rows all reach the LLM provider at nearly the same instant, which
# can trip its rate limiter (429) or a transient network hiccup — classify_row
# retries those a few times with exponential backoff + jitter before giving up
# on the row, instead of immediately recording a blank prediction.
MAX_ATTEMPTS = 4
BASE_DELAY_SECONDS = 2.0


def _build_classifier(method: str, top_k: int, model_id: str | None):
    """Return an async (message) -> (main_category, sub_category) callable.
    top_k only applies to the 'vector'/'finetuned_vector'/'examples'
    methods. model_id is required for 'finetuned'/'finetuned_vector' — the
    ft:... model ID from domain/helpdesk/scripts/run_finetune_job.py."""
    if method == "prompt":
        return classify_ticket_category
    if method == "vector":
        return lambda message: classify_ticket_category_vector(message, k=top_k)
    if method == "examples":
        return lambda message: classify_ticket_category_examples(message, k=top_k)
    if method == "pipeline":
        # Drops the confidence score run_prediction_detail.py-style scripts
        # care about — this eval-loop's classify_row() only expects a
        # (main_category, sub_category) pair. See
        # domain/helpdesk/scripts/run_pipeline_eval.py for the confidence-aware
        # per-ticket capture of this same classifier.
        async def _pipeline_classify(message: str) -> tuple[str, str]:
            main_category, sub_category, _confidence = await classify_ticket_category_pipeline(
                message, k=top_k
            )
            return main_category, sub_category

        return _pipeline_classify
    if method in ("finetuned", "finetuned_vector") and not model_id:
        raise SystemExit(f"--method {method} requires --model <ft:...>")
    if method == "finetuned":
        return lambda message: classify_ticket_category_finetuned(message, model_id)
    return lambda message: classify_ticket_category_finetuned_vector(message, model_id, k=top_k)


async def classify_row(
    sem: asyncio.Semaphore, idx: int, message: str, classify
) -> tuple[int, str, str]:
    async with sem:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                main_category, sub_category = await classify(str(message))
                return idx, main_category, sub_category
            except Exception as exc:
                if attempt == MAX_ATTEMPTS:
                    print(f"[row {idx}] ERROR after {MAX_ATTEMPTS} attempts: {exc}")
                    return idx, "", ""
                delay = BASE_DELAY_SECONDS * (2 ** (attempt - 1)) + random.uniform(0, 1)
                print(f"[row {idx}] attempt {attempt} failed ({exc}); retrying in {delay:.1f}s")
                await asyncio.sleep(delay)


async def run(args):
    full_df = pd.read_excel(args.input)

    has_sub_category = bool(args.subcategory_col)

    required_cols = {args.message_col, args.category_col}
    if has_sub_category:
        required_cols.add(args.subcategory_col)
    missing = required_cols - set(full_df.columns)
    if missing:
        raise SystemExit(f"Input file is missing expected column(s): {missing}")

    start_row = args.start_row
    end_row = args.end_row if args.end_row is not None else len(full_df)

    cols_to_keep = [args.message_col, args.category_col]
    rename_map = {args.message_col: "message", args.category_col: "true_category"}
    if has_sub_category:
        cols_to_keep.append(args.subcategory_col)
        rename_map[args.subcategory_col] = "true_sub_category"

    df = (
        full_df.iloc[start_row:end_row][cols_to_keep]
        .rename(columns=rename_map)
        .reset_index(drop=True)
    )

    if df.empty:
        raise SystemExit(
            f"No rows in range [{start_row}:{end_row}) — file has {len(full_df)} total rows"
        )

    classify = _build_classifier(args.method, args.top_k, args.model)
    k_note = f", top_k={args.top_k}" if args.method in ("vector", "finetuned_vector", "examples") else ""
    model_note = f", model={args.model}" if args.model else ""
    print(f"Testing rows [{start_row}:{end_row}) → {len(df)} rows (method={args.method}{k_note}{model_note})")

    sem = asyncio.Semaphore(args.concurrency)
    tasks = [
        classify_row(sem, idx, row["message"], classify) for idx, row in df.iterrows()
    ]

    predicted_category = [""] * len(df)
    predicted_sub_category = [""] * len(df)

    done = 0
    for coro in asyncio.as_completed(tasks):
        idx, main_category, sub_category = await coro
        predicted_category[idx] = main_category
        predicted_sub_category[idx] = sub_category
        done += 1
        if done % 10 == 0 or done == len(df):
            print(f"classified {done}/{len(df)}")

    df["predicted_category"] = predicted_category
    df["predicted_sub_category"] = predicted_sub_category

    # Normalize whitespace before comparing — source data has inconsistent
    # spacing (e.g. "CRM SR & TT  Issues" vs "CRM SR & TT Issues").
    true_norm = df["true_category"].str.strip().str.replace(r"\s+", " ", regex=True)
    pred_norm = df["predicted_category"].str.strip().str.replace(r"\s+", " ", regex=True)
    df["correct"] = true_norm == pred_norm

    overall_accuracy = df["correct"].mean() * 100
    print(f"\nOverall category accuracy: {overall_accuracy:.1f}% ({df['correct'].sum()}/{len(df)})")

    if has_sub_category:
        true_sub_norm = df["true_sub_category"].str.strip().str.replace(r"\s+", " ", regex=True)
        pred_sub_norm = df["predicted_sub_category"].str.strip().str.replace(r"\s+", " ", regex=True)
        df["sub_correct"] = true_sub_norm == pred_sub_norm
        overall_sub_accuracy = df["sub_correct"].mean() * 100
        print(
            f"Overall sub-category accuracy: {overall_sub_accuracy:.1f}% "
            f"({df['sub_correct'].sum()}/{len(df)})"
        )

    per_category = df.groupby(true_norm).agg(
        n=("correct", "size"),
        correct=("correct", "sum"),
    )
    per_category["accuracy_pct"] = (per_category["correct"] / per_category["n"] * 100).round(1)
    per_category = per_category.sort_values("n", ascending=False)

    confusion = (
        df[~df["correct"]]
        .groupby([true_norm, pred_norm])
        .size()
        .reset_index(name="count")
        .sort_values("count", ascending=False)
    )
    confusion.columns = ["true_category", "predicted_category", "count"]

    with pd.ExcelWriter(args.output) as writer:
        df.to_excel(writer, sheet_name="results", index=False)
        per_category.to_excel(writer, sheet_name="per_category_accuracy")
        confusion.to_excel(writer, sheet_name="misclassifications", index=False)

    print(f"\nWrote results to {args.output}")
    print()
    print(per_category.to_string())
    print()
    print("Top misclassifications:")
    print(confusion.head(15).to_string(index=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="domain/helpdesk/data/DATA_Set.xlsx")
    parser.add_argument("--output", default="domain/helpdesk/data/eval_results.xlsx")
    parser.add_argument("--message-col", default="message", help="Column holding the ticket message text")
    parser.add_argument("--category-col", default="Type", help="Column holding the ground-truth main category")
    parser.add_argument(
        "--subcategory-col",
        default=None,
        help="Column holding the ground-truth sub-category, if the input file has one "
        "(e.g. INC_SUB_TYPE_CD). Omit to skip sub-category accuracy entirely.",
    )
    parser.add_argument("--start-row", type=int, default=0, help="First row (0-indexed) of the input file to test")
    parser.add_argument("--end-row", type=int, default=500, help="Row to stop before (exclusive) — omit/None to run to the end of the file")
    parser.add_argument("--concurrency", type=int, default=5, help="Max concurrent LLM calls")
    parser.add_argument(
        "--method",
        choices=["prompt", "vector", "finetuned", "finetuned_vector", "examples", "pipeline"],
        default="prompt",
        help="Classifier to evaluate: 'prompt' (current list_categories_tool "
        "flow, default), 'vector' (vector-retrieval over the hand-written KB "
        "+ top-k candidates), 'finetuned' (fine-tuned model, no category "
        "list/candidates shown), 'finetuned_vector' (top-k candidates, "
        "decided by the fine-tuned model instead of the base model), "
        "'examples' (top-k most similar REAL historical tickets instead of "
        "the hand-written KB — see domain/helpdesk/scripts/"
        "ingest_ticket_examples.py), or 'pipeline' (Hybrid Retrieval [KB + "
        "examples merged/reranked] -> Hierarchical LLM Classifier -> "
        "self-consistency confidence check — see classify_ticket_category_"
        "pipeline() and domain/helpdesk/scripts/run_pipeline_eval.py for the "
        "confidence-aware detail version of this same run). "
        "'vector'/'finetuned_vector' require domain/helpdesk/scripts/"
        "ingest_category_kb.py to have been run; 'finetuned'/"
        "'finetuned_vector' require --model; 'examples'/'pipeline' require "
        "domain/helpdesk/scripts/ingest_ticket_examples.py.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=5,
        help="Number of candidates retrieved/predicted before the LLM "
        "picks one. Only used when --method vector/finetuned_vector/examples.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Fine-tuned model ID (ft:...) from domain/helpdesk/scripts/run_finetune_job.py. "
        "Required when --method finetuned or finetuned_vector.",
    )
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
