"""
Prepare training/test data for fine-tuning a category classifier from
domain/helpdesk/data/Incidents_Feb.xlsx.

Uses ONLY the DESCRIPTION column as input text — NOT DESCRIPTION | COMMENT.
COMMENT is a post-resolution agent note ("Completed", "Duplicate ref X")
written after a ticket is investigated; it will never exist when the model
classifies a brand-new incoming ticket in production, so training on it
would leak information unavailable at real prediction time.

Every row in Incidents_Feb.xlsx has both INCIDENT_TYPE_CD (main category)
and INC_SUB_TYPE_CD (sub-category), so every training example gets both a
"**Category:**" and "**Sub-category:**" target line, matching the output
contract domain.helpdesk.pipeline.category_classification._parse_category_draft()
already parses.

Splits per category: a fixed held-out TEST_FRACTION goes to the test set
(floor MIN_TEST_ROWS per category so rare categories like OSS Enterprise
Services Issues still get evaluated), everything else goes to training —
uncapped, so the largest/lowest-accuracy categories (e.g. CRM OM - After
Submit Issues) keep every available example instead of being downsampled.

Usage (from /app inside the backend container):
    python domain/helpdesk/scripts/prepare_finetune_data.py
    python domain/helpdesk/scripts/prepare_finetune_data.py \
        --input domain/helpdesk/data/Incidents_Feb.xlsx \
        --train-output domain/helpdesk/data/finetune_train.jsonl \
        --test-output domain/helpdesk/data/finetune_test.xlsx
"""

import argparse
import json

import pandas as pd

TEST_FRACTION = 0.10
MIN_TEST_ROWS = 10

SYSTEM_PROMPT = (
    "You are the SLT Mobitel Help Desk category classifier. Given the "
    "customer's issue, reply with EXACTLY these two lines, nothing else:\n"
    "**Category:** <category_name>\n"
    "**Sub-category:** <subcategory>"
)


def _build_example(text: str, main_category: str, sub_category: str) -> dict:
    assistant_content = f"**Category:** {main_category}\n**Sub-category:** {sub_category}"
    return {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
            {"role": "assistant", "content": assistant_content},
        ]
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="domain/helpdesk/data/Incidents_Feb.xlsx")
    parser.add_argument("--train-output", default="domain/helpdesk/data/finetune_train.jsonl")
    parser.add_argument("--test-output", default="domain/helpdesk/data/finetune_test.xlsx")
    parser.add_argument(
        "--max-train-per-category",
        type=int,
        default=None,
        help="Cap the number of TRAINING examples kept per category, for a "
        "smaller/cheaper first fine-tuning run (e.g. 300). The held-out "
        "test split is never capped, so it stays comparable across runs "
        "with different caps. Categories with fewer rows than the cap keep "
        "everything they have.",
    )
    args = parser.parse_args()

    df = pd.read_excel(args.input)
    df["main_category"] = df["INCIDENT_TYPE_CD"].str.strip().str.replace(r"\s+", " ", regex=True)
    df["sub_category"] = df["INC_SUB_TYPE_CD"].str.strip().str.replace(r"\s+", " ", regex=True)
    df["text"] = df["DESCRIPTION"].astype(str).str.strip()
    df = df[df["text"].str.len() > 0]

    train_rows: list[dict] = []
    test_rows: list[dict] = []
    counts: list[dict] = []

    for category, group in df.groupby("main_category"):
        group = group.sample(frac=1.0, random_state=42)  # shuffle
        n_test = max(MIN_TEST_ROWS, round(len(group) * TEST_FRACTION))
        n_test = min(n_test, len(group) - 1) if len(group) > 1 else 0

        test_group = group.iloc[:n_test]
        train_group = group.iloc[n_test:]
        if args.max_train_per_category is not None:
            train_group = train_group.iloc[: args.max_train_per_category]

        train_rows.extend(train_group.to_dict("records"))
        test_rows.extend(test_group.to_dict("records"))
        counts.append(
            {"category": category, "total": len(group), "train": len(train_group), "test": len(test_group)}
        )

    counts_df = pd.DataFrame(counts).sort_values("total", ascending=False)
    print("Per-category train/test split:")
    print(counts_df.to_string(index=False))
    print()
    print(f"Total: {len(train_rows)} train / {len(test_rows)} test / {len(df)} overall")

    with open(args.train_output, "w", encoding="utf-8") as f:
        for row in train_rows:
            example = _build_example(row["text"], row["main_category"], row["sub_category"])
            f.write(json.dumps(example, ensure_ascii=False) + "\n")
    print(f"\nWrote {len(train_rows)} training examples to {args.train_output}")

    test_df = pd.DataFrame(test_rows)[["text", "main_category", "sub_category"]].rename(
        columns={"text": "message", "main_category": "true_category", "sub_category": "true_sub_category"}
    )
    test_df.to_excel(args.test_output, index=False)
    print(f"Wrote {len(test_df)} held-out test rows to {args.test_output}")


if __name__ == "__main__":
    main()
