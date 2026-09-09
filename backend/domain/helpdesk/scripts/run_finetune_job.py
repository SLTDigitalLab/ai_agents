"""
Kick off and monitor an OpenAI fine-tuning job for the category classifier,
using the JSONL produced by domain/helpdesk/scripts/prepare_finetune_data.py.

This is the one step in the fine-tuning pipeline that costs real training
money and takes real wall-clock time (minutes to a couple hours) — review
prepare_finetune_data.py's printed per-category counts before running this.

Uses the `openai` Python SDK directly (installed as a dependency of
langchain-openai; not a separate requirements.txt entry).

Usage (from /app inside the backend container):
    python domain/helpdesk/scripts/run_finetune_job.py
    python domain/helpdesk/scripts/run_finetune_job.py --train-file domain/helpdesk/data/finetune_train.jsonl \
        --base-model gpt-4o-mini-2024-07-18
"""

import argparse
import sys
import time

from openai import OpenAI

sys.path.insert(0, "/app")

from core.config import settings  # noqa: E402

POLL_SECONDS = 20


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-file", default="domain/helpdesk/data/finetune_train.jsonl")
    parser.add_argument("--base-model", default="gpt-4o-mini-2024-07-18")
    parser.add_argument(
        "--suffix",
        default="helpdesk-category",
        help="Short label appended to the resulting model name for identification",
    )
    args = parser.parse_args()

    api_key = settings.LLM_API_KEY or settings.OPENAI_API_KEY
    client = OpenAI(api_key=api_key)

    print(f"Uploading {args.train_file}...")
    with open(args.train_file, "rb") as f:
        uploaded = client.files.create(file=f, purpose="fine-tune")
    print(f"Uploaded file id: {uploaded.id}")

    print(f"Creating fine-tuning job (base model={args.base_model})...")
    job = client.fine_tuning.jobs.create(
        training_file=uploaded.id,
        model=args.base_model,
        suffix=args.suffix,
    )
    print(f"Job id: {job.id} — status: {job.status}")

    while True:
        job = client.fine_tuning.jobs.retrieve(job.id)
        print(f"[{time.strftime('%H:%M:%S')}] status: {job.status}")
        if job.status in ("succeeded", "failed", "cancelled"):
            break
        time.sleep(POLL_SECONDS)

    if job.status != "succeeded":
        print(f"\nJob did not succeed: {job.status}")
        for event in client.fine_tuning.jobs.list_events(job.id, limit=20).data:
            print(f"  {event.created_at} {event.message}")
        raise SystemExit(1)

    print(f"\nFine-tuning succeeded. Model ID:\n{job.fine_tuned_model}")
    print(
        "\nUse it with:\n"
        f"  python domain/helpdesk/scripts/run_accuracy_eval.py --method finetuned "
        f"--model {job.fine_tuned_model} --input domain/helpdesk/data/finetune_test.xlsx "
        f"--category-col true_category --subcategory-col true_sub_category"
    )


if __name__ == "__main__":
    main()
