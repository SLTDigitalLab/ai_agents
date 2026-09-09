"""
Per-ticket prediction detail run: for each ticket, records the top-k
categories retrieved from the category-KB vector search AND the final
category the LLM chose from those candidates. Unlike run_accuracy_eval.py
(which only keeps the final prediction), this keeps the full candidate
list so a supervisor-facing report can show "what the retriever surfaced"
vs. "what the model picked" side by side per ticket.

Reuses the exact live classification path (search_category_candidates +
category_vector_system_prompt + _resolve_category) that
classify_ticket_category_vector() in
domain.helpdesk.pipeline.category_classification uses, but inlined here so
the intermediate candidates aren't discarded.

Must run inside the backend container/environment (same requirement as
run_accuracy_eval.py) — it imports the agent's LLM bindings and hits the
live Qdrant/Postgres category stores.

Usage (from /app inside the backend container):
    python domain/helpdesk/scripts/run_prediction_detail.py \
        --input domain/helpdesk/data/representative_eval_200.xlsx \
        --output domain/helpdesk/data/prediction_detail_200.xlsx \
        --top-k 5 --concurrency 5
"""

import argparse
import asyncio
import random
import sys

import pandas as pd

sys.path.insert(0, "/app")

from domain.helpdesk.pipeline.helpers import llm, _message_to_text  # noqa: E402
from domain.helpdesk.pipeline.category_classification import (  # noqa: E402
    _parse_category_draft,
    _resolve_category,
    _load_valid_categories,
)
from domain.helpdesk.prompts import category_vector_system_prompt  # noqa: E402
from domain.helpdesk.tools.category_kb_tools import search_category_candidates  # noqa: E402

MAX_ATTEMPTS = 4
BASE_DELAY_SECONDS = 2.0


async def classify_with_candidates(
    message: str, k: int, valid_categories: list[tuple[str, str, str, str]]
) -> tuple[list[dict], str, str]:
    """Same flow as classify_ticket_category_vector(), but also returns the
    raw top-k candidate list so callers can report it alongside the LLM's
    final pick."""
    candidates = await search_category_candidates(message, k=k)

    if not candidates:
        main_category, sub_category = _resolve_category("", "", message, valid_categories)
        return [], main_category, sub_category

    blocks = []
    for i, c in enumerate(candidates, start=1):
        lines = [
            f"{i}. Category: {c.get('main_category', 'N/A')} | "
            f"Sub-category: {c.get('sub_category', 'N/A')}",
        ]
        if c.get("description"):
            lines.append(f"   Description: {c['description']}")
        if c.get("customer_expressions"):
            lines.append(f"   Customer expressions: {c['customer_expressions']}")
        if c.get("symptoms"):
            lines.append(f"   Symptoms: {c['symptoms']}")
        if c.get("similar_categories"):
            lines.append(f"   Similar categories (don't confuse with): {c['similar_categories']}")
        if c.get("do_not_use_when"):
            lines.append(f"   Do not use when: {c['do_not_use_when']}")
        blocks.append("\n".join(lines))
    candidates_text = "\n\n".join(blocks)

    system_prompt = {
        "role": "system",
        "content": category_vector_system_prompt(message, candidates_text),
    }
    response = await llm.ainvoke([system_prompt])
    reply = _message_to_text(response)
    raw_main, raw_sub = _parse_category_draft(reply)
    main_category, sub_category = _resolve_category(raw_main, raw_sub, message, valid_categories)

    return candidates, main_category, sub_category


async def classify_row(sem: asyncio.Semaphore, idx: int, message: str, k: int, valid_categories):
    async with sem:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                candidates, main_category, sub_category = await classify_with_candidates(
                    str(message), k, valid_categories
                )
                return idx, candidates, main_category, sub_category
            except Exception as exc:
                if attempt == MAX_ATTEMPTS:
                    print(f"[row {idx}] ERROR after {MAX_ATTEMPTS} attempts: {exc}")
                    return idx, [], "", ""
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

    valid_categories = _load_valid_categories("run_prediction_detail")
    print(f"Loaded {len(valid_categories)} valid categories from Postgres")

    k = args.top_k
    print(f"Testing rows [{start_row}:{end_row}) → {len(df)} rows, top_k={k}")

    sem = asyncio.Semaphore(args.concurrency)
    tasks = [
        classify_row(sem, idx, row["message"], k, valid_categories)
        for idx, row in df.iterrows()
    ]

    candidates_by_row = [[] for _ in range(len(df))]
    predicted_category = [""] * len(df)
    predicted_sub_category = [""] * len(df)

    done = 0
    for coro in asyncio.as_completed(tasks):
        idx, candidates, main_category, sub_category = await coro
        candidates_by_row[idx] = candidates
        predicted_category[idx] = main_category
        predicted_sub_category[idx] = sub_category
        done += 1
        if done % 10 == 0 or done == len(df):
            print(f"classified {done}/{len(df)}")

    # Flatten top-k candidates into cand{i}_category/subcategory/score columns.
    for i in range(k):
        df[f"cand{i+1}_category"] = [
            (c[i].get("main_category", "") if len(c) > i else "") for c in candidates_by_row
        ]
        df[f"cand{i+1}_subcategory"] = [
            (c[i].get("sub_category", "") if len(c) > i else "") for c in candidates_by_row
        ]
        df[f"cand{i+1}_score"] = [
            (round(float(c[i].get("_score", 0.0)), 4) if len(c) > i else None)
            for c in candidates_by_row
        ]

    df["predicted_category"] = predicted_category
    df["predicted_sub_category"] = predicted_sub_category

    true_norm = df["true_category"].astype(str).str.strip().str.replace(r"\s+", " ", regex=True)
    pred_norm = df["predicted_category"].astype(str).str.strip().str.replace(r"\s+", " ", regex=True)
    df["correct"] = true_norm == pred_norm

    # Recall@k: was the true category anywhere among the retrieved candidates,
    # regardless of what the LLM ultimately picked?
    def _true_in_candidates(row):
        true_cat = str(row["true_category"]).strip()
        cand_cats = [
            str(row.get(f"cand{i+1}_category", "")).strip() for i in range(k)
        ]
        return true_cat in cand_cats

    df["true_category_in_top_k"] = df.apply(_true_in_candidates, axis=1)

    overall_accuracy = df["correct"].mean() * 100
    recall_at_k = df["true_category_in_top_k"].mean() * 100
    print(f"\nFinal (LLM-chosen) category accuracy: {overall_accuracy:.1f}% ({df['correct'].sum()}/{len(df)})")
    print(f"Recall@{k} (true category anywhere in retrieved candidates): {recall_at_k:.1f}%")

    with pd.ExcelWriter(args.output) as writer:
        df.to_excel(writer, sheet_name="results", index=False)

    print(f"\nWrote per-ticket detail to {args.output}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="domain/helpdesk/data/representative_eval_200.xlsx")
    parser.add_argument("--output", default="domain/helpdesk/data/prediction_detail_200.xlsx")
    parser.add_argument("--start-row", type=int, default=0)
    parser.add_argument("--end-row", type=int, default=None)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--concurrency", type=int, default=5)
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
