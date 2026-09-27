"""
One-off seed of helpdesk.all_categories from an export spreadsheet.

domain/helpdesk/data/All-categories_for_agent_v3.xlsx is a snapshot export of the
helpdesk.all_categories table (same columns: category_id, category_name,
subcategory, description, keywords, example_tickets). Use this to repopulate
a fresh Postgres instance (e.g. a new local dev environment) so
list_categories()-backed flows — draft_ticket()'s list_categories_tool AND
classify_ticket_category_vector()'s DB-truth validation — have real data to
validate against instead of an empty table.

Usage (from /app inside the backend container):
    python domain/helpdesk/scripts/seed_categories.py
    python domain/helpdesk/scripts/seed_categories.py --input domain/helpdesk/data/All-categories_for_agent_v3.xlsx
"""

import argparse
import sys

import pandas as pd

sys.path.insert(0, "/app")

from services.helpdesk_tickets import create_category, list_categories  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        default="domain/helpdesk/data/All-categories_for_agent_v3.xlsx",
        help="Path to the all_categories export spreadsheet",
    )
    args = parser.parse_args()

    existing = list_categories(limit=1000)
    if existing:
        print(f"helpdesk.all_categories already has {len(existing)} row(s) — skipping seed.")
        return

    df = pd.read_excel(args.input, sheet_name="Categories")

    created = 0
    for _, row in df.iterrows():
        create_category(
            category_id=str(row["category_id"]),
            category_name=str(row["category_name"]),
            subcategory=str(row["subcategory"]),
            description=str(row["description"]) if pd.notna(row["description"]) else None,
            keywords=str(row["keywords"]) if pd.notna(row["keywords"]) else None,
            example_tickets=str(row["example_tickets"]) if pd.notna(row["example_tickets"]) else None,
        )
        created += 1

    print(f"Seeded {created} categor{'y' if created == 1 else 'ies'} into helpdesk.all_categories")


if __name__ == "__main__":
    main()
