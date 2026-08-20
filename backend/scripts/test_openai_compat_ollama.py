"""Smoke test for an OpenAI-compatible Ollama gateway.

This script sends a single chat completion request through the OpenAI Python
client using a custom base URL. It is meant to verify that an external
application can talk to the local Ollama endpoint through an OpenAI-compatible
proxy.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI


def _load_environment() -> None:
    """Load .env from the repository root and backend folder if present."""
    repo_root = Path(__file__).resolve().parents[2]
    backend_root = Path(__file__).resolve().parents[1]

    load_dotenv(repo_root / ".env", override=False)
    load_dotenv(backend_root / ".env", override=False)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Test an OpenAI-compatible Ollama endpoint with chat.completions.create()."
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("SLM_BASE_URL", "https://sltollama.duckdns.org/devapi/v1"),
        help="OpenAI-compatible base URL for the Ollama proxy.",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("SLM_API_KEY", ""),
        help="API key for the proxy.",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("SLM_MODEL", "llama3.2:1b"),
        help="Model name to request.",
    )
    parser.add_argument(
        "--prompt",
        default=os.getenv(
            "OLLAMA_TEST_PROMPT",
            "Reply with exactly: ollama connection ok",
        ),
        help="Prompt sent to the model.",
    )
    return parser.parse_args()


def main() -> int:
    _load_environment()
    args = _parse_args()

    if not args.api_key:
        print(
            "Missing API key. Set OLLAMA_API_KEY or pass --api-key.",
            file=sys.stderr,
        )
        return 2

    client = OpenAI(
        base_url=args.base_url.rstrip("/"),
        api_key=args.api_key,
        timeout=30.0,
    )

    try:
        response = client.chat.completions.create(
            model=args.model,
            messages=[
                {"role": "user", "content": "tell me a joke"},
            ],
        )
    except Exception as exc:
        print(f"Request failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    choice = response.choices[0] if response.choices else None
    content = choice.message.content if choice and choice.message else None

    output = {
        "ok": True,
        "base_url": args.base_url.rstrip("/"),
        "model": args.model,
        "response_id": getattr(response, "id", None),
        "content": content,
    }
    print(json.dumps(output, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())