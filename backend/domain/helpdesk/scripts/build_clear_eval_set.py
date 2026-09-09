"""
Build a "clear-message" evaluation set: held-out tickets whose category is
genuinely determinable from the text alone, as opposed to the structurally
ambiguous cases documented in domain/helpdesk/data/category_accuracy_report.html
(same real event, described from different backend systems — e.g. the
SOA / CRM OM - After Submit / Clarity OSS cluster).

Definition of "clear", made objective rather than eyeballed: for each
held-out test ticket, retrieve its k nearest real tickets from the ALREADY
INGESTED training corpus (domain.helpdesk.tools.ticket_example_tools —
helpdesk_ticket_examples_docs, ingested by ingest_ticket_examples.py, which
excludes these same held-out test rows already — no leakage). If most of
those neighbors share the ticket's own true category, the category is
"legible" from the text: similar wording reliably co-occurs with the same
answer. If neighbors disagree, the ticket sits in an ambiguous zone no
text-only classifier can resolve — exactly what run_accuracy_eval.py's
prompt/vector/examples methods have been measured against so far,
unfiltered.

This produces a SEPARATE benchmark answering "how good is the classifier
when the answer is actually in the text?" — distinct from (and expected to
score higher than) the overall/unfiltered accuracy numbers already measured.

Usage (from /app inside the backend container):
    python domain/helpdesk/scripts/build_clear_eval_set.py
    python domain/helpdesk/scripts/build_clear_eval_set.py --n 200 --k 5 --min-agreement 0.8
"""

import argparse
import asyncio
import sys

import pandas as pd

sys.path.insert(0, "/app")

from domain.helpdesk.tools.ticket_example_tools import search_ticket_examples  # noqa: E402


async def _score_row(sem: asyncio.Semaphore, idx: int, message: str, true_category: str, k: int):
    async with sem:
        neighbors = await search_ticket_examples(message, k=k)
        if not neighbors:
            return idx, 0.0, 0
        agree = sum(1 for n in neighbors if n.get("main_category") == true_category)
        return idx, agree / len(neighbors), len(neighbors)


async def run(args) -> None:
    df = pd.read_excel(args.input)
    print(f"Candidate held-out tickets: {len(df)}")

    sem = asyncio.Semaphore(args.concurrency)
    tasks = [
        _score_row(sem, idx, row["message"], row["true_category"], args.k)
        for idx, row in df.iterrows()
    ]

    agreement = [0.0] * len(df)
    neighbor_count = [0] * len(df)
    done = 0
    for coro in asyncio.as_completed(tasks):
        idx, score, n_neighbors = await coro
        agreement[idx] = score
        neighbor_count[idx] = n_neighbors
        done += 1
        if done % 100 == 0 or done == len(df):
            print(f"  scored {done}/{len(df)}")

    df["neighbor_agreement"] = agreement
    df["neighbors_checked"] = neighbor_count

    rare_categories = set(args.rare_categories or [])

    strict_pool = df[
        (df["neighbor_agreement"] >= args.min_agreement) & (~df["true_category"].isin(rare_categories))
    ].sort_values("neighbor_agreement", ascending=False)
    print(
        f"\nTickets clearing the {args.min_agreement:.0%} neighbor-agreement bar "
        f"(excluding rare categories): {len(strict_pool)} / {len(df)}"
    )

    selected_parts = [strict_pool.head(args.n)]

    # Rare categories are too small in volume for consistent neighbor
    # agreement (see module docstring) — include their best-available
    # tickets regardless of the bar, up to --rare-quota each, so they're
    # represented at all rather than silently absent. Marked explicitly so
    # anyone reading the output knows these are lower-confidence rows.
    for cat in rare_categories:
        cat_pool = df[df["true_category"] == cat].sort_values("neighbor_agreement", ascending=False)
        n_above_bar = (cat_pool["neighbor_agreement"] >= args.min_agreement).sum()
        take = cat_pool.head(args.rare_quota)
        print(
            f"Rare category '{cat}': {len(cat_pool)} candidates total, "
            f"{n_above_bar} clear the {args.min_agreement:.0%} bar — "
            f"including best {len(take)} regardless of bar"
        )
        selected_parts.append(take)

    selected = pd.concat(selected_parts, ignore_index=True)
    selected["below_bar"] = selected["neighbor_agreement"] < args.min_agreement
    selected = selected.drop_duplicates(subset=["message", "true_category"]).reset_index(drop=True)

    print(f"\nSelected {len(selected)} rows total (target was {args.n} + rare-category quotas)")
    print(f"  of which {selected['below_bar'].sum()} are below-bar rare-category rows (flagged in 'below_bar' column)")
    print("\nCategory distribution of the selected set:")
    print(selected["true_category"].value_counts())

    selected.to_excel(args.output, index=False)
    print(f"\nWrote eval set to {args.output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        default="domain/helpdesk/data/held_out_test.xlsx",
        help="Held-out test file to draw candidates from (message/true_category/true_sub_category columns)",
    )
    parser.add_argument("--output", default="domain/helpdesk/data/clear_eval_500.xlsx")
    parser.add_argument("--n", type=int, default=500, help="Number of clear messages to select (true count reported, not padded if fewer qualify)")
    parser.add_argument("--k", type=int, default=5, help="Neighbors checked per candidate")
    parser.add_argument(
        "--min-agreement",
        type=float,
        default=0.8,
        help="Minimum fraction of neighbors that must share the ticket's true category (0.8 = 4/5)",
    )
    parser.add_argument(
        "--rare-categories",
        nargs="*",
        default=["MDM", "OSS Enterprise Services Issues"],
        help="Categories too low-volume for reliable neighbor agreement — their "
        "best-available tickets are included regardless of --min-agreement, "
        "flagged in the output's 'below_bar' column.",
    )
    parser.add_argument(
        "--rare-quota",
        type=int,
        default=15,
        help="Max tickets to include per rare category, regardless of agreement bar",
    )
    parser.add_argument("--concurrency", type=int, default=10)
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
