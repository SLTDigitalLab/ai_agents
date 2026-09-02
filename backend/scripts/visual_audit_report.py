"""Summarize Visual RAG extraction coverage from persistent audit logs.

Run from the backend directory:
    python scripts/visual_audit_report.py
"""

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from core.config import evidence_storage_dir


def main() -> None:
    audit_dir = evidence_storage_dir() / "audits"
    if not audit_dir.exists():
        print("No visual audit records found. Re-ingest PDF documents first.")
        return

    totals = Counter()
    by_document = defaultdict(Counter)

    for audit_file in audit_dir.glob("*.jsonl"):
        for line in audit_file.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            status = record.get("status", "unknown")
            totals[status] += 1
            by_document[record.get("source_file", audit_file.stem)][status] += 1

    print("Visual RAG audit summary")
    print(f"Indexed pages: {totals['indexed']}")
    print(f"Skipped pages: {totals['skipped']}")
    print("\nDocuments with skipped pages:")
    for source_file, counts in sorted(by_document.items()):
        if counts["skipped"]:
            print(
                f"- {source_file}: indexed={counts['indexed']}, "
                f"skipped={counts['skipped']}"
            )


if __name__ == "__main__":
    main()