import os
import sys
import time
import subprocess
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from qdrant_client import QdrantClient


# ---------------------------------------------------------------------
# Paths + .env
# ---------------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")


# ---------------------------------------------------------------------
# Backend/Admin config
# ---------------------------------------------------------------------
# If running local uvicorn:
# ADMIN_BASE_URL=http://127.0.0.1:8000
#
# If using Docker backend exposed as 127.0.0.1:8100->8000:
# ADMIN_BASE_URL=http://127.0.0.1:8100
ADMIN_BASE_URL = os.getenv("ADMIN_BASE_URL", "http://127.0.0.1:8000").rstrip("/")

ADMIN_USER_EMAIL = os.getenv("ADMIN_USER_EMAIL")

LIFESTORE_AGENT_NAME = os.getenv("LIFESTORE_AGENT_NAME", "ask_lifestore")
SLT_ENTERPRISE_AGENT_NAME = os.getenv(
    "SLT_ENTERPRISE_AGENT_NAME",
    "ask_slt_enterprise",
)

# ---------------------------------------------------------------------
# Qdrant collection names
# ---------------------------------------------------------------------
# These are the names we send to /ingest-url.
# Your backend/ingestion service may internally create <name>_docs.
LIFESTORE_COLLECTION_NAME = os.getenv(
    "LIFESTORE_QDRANT_COLLECTION",
    "lifestore",
)

ENTERPRISE_COLLECTION_NAME = os.getenv(
    "ENTERPRISE_QDRANT_COLLECTION",
    "enterprise",
)

# These are the actual Qdrant collections to delete before monthly refresh.
# Since your backend auto-adds "_docs", these default to:
# lifestore_docs and enterprise_docs
LIFESTORE_DELETE_COLLECTION_NAME = os.getenv(
    "LIFESTORE_QDRANT_DELETE_COLLECTION",
    f"{LIFESTORE_COLLECTION_NAME}_docs",
)

ENTERPRISE_DELETE_COLLECTION_NAME = os.getenv(
    "ENTERPRISE_QDRANT_DELETE_COLLECTION",
    f"{ENTERPRISE_COLLECTION_NAME}_docs",
)

# Optional safety: also delete the base collection if it exists.
# This helps if you accidentally created both "lifestore" and "lifestore_docs".
DELETE_BASE_COLLECTION_TOO = (
    os.getenv("DELETE_BASE_QDRANT_COLLECTION_TOO", "true").lower() == "true"
)

CLEAR_QDRANT_BEFORE_INGEST = (
    os.getenv("CLEAR_QDRANT_BEFORE_INGEST", "true").lower() == "true"
)

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")


# ---------------------------------------------------------------------
# URLs to ingest
# ---------------------------------------------------------------------
LIFESTORE_URL = os.getenv(
    "LIFESTORE_INGEST_URL",
    "https://lifestore.lk/products?categories=All&products",
)

SLT_ENTERPRISE_URL = os.getenv(
    "SLT_ENTERPRISE_INGEST_URL",
    "https://www.slt.lk/en/business/enterprises",
)


# ---------------------------------------------------------------------
# Crawl settings
# ---------------------------------------------------------------------
# For testing, reduce these.
# For real monthly automation, keep them higher.
LIFESTORE_MAX_PAGES = int(os.getenv("LIFESTORE_INGEST_MAX_PAGES", "1000"))
LIFESTORE_MAX_DEPTH = int(os.getenv("LIFESTORE_INGEST_MAX_DEPTH", "5"))

ENTERPRISE_MAX_PAGES = int(os.getenv("ENTERPRISE_INGEST_MAX_PAGES", "500"))
ENTERPRISE_MAX_DEPTH = int(os.getenv("ENTERPRISE_INGEST_MAX_DEPTH", "4"))

POLL_SECONDS = int(os.getenv("INGESTION_POLL_SECONDS", "20"))

RUN_QDRANT_INGESTIONS = (
    os.getenv("RUN_QDRANT_INGESTIONS", "true").lower() == "true"
)

RUN_NEO4J_GRAPH_REFRESH = (
    os.getenv("RUN_NEO4J_GRAPH_REFRESH", "true").lower() == "true"
)


# ---------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------
INGEST_URL_ENDPOINT = f"{ADMIN_BASE_URL}/api/v1/admin/ingest-url"
INGESTION_STATUS_ENDPOINT = f"{ADMIN_BASE_URL}/api/v1/admin/ingestion-status"


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def require_env(name: str, value: str | None):
    if not value:
        raise RuntimeError(f"{name} must be set in .env")


def print_section(title: str):
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def request_json(method: str, url: str, **kwargs) -> dict[str, Any]:
    try:
        response = requests.request(method, url, timeout=30, **kwargs)
    except requests.RequestException as exc:
        raise RuntimeError(
            f"Request failed for {method} {url}: {type(exc).__name__}: {exc}"
        ) from exc

    if response.status_code >= 400:
        raise RuntimeError(
            f"Request failed for {method} {url}: "
            f"{response.status_code} {response.text}"
        )

    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"Response was not valid JSON for {method} {url}: {response.text}"
        ) from exc


def get_qdrant_client() -> QdrantClient:
    if QDRANT_API_KEY:
        return QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)

    return QdrantClient(url=QDRANT_URL)


def check_backend_is_available():
    print_section("Checking backend admin API")

    status = request_json("GET", INGESTION_STATUS_ENDPOINT)

    print("Backend is reachable.")
    print(f"Admin base URL: {ADMIN_BASE_URL}")
    print(f"Current ingestion status: {status}")

    if status.get("active"):
        raise RuntimeError(
            "Another ingestion job is already active. "
            "Stop it or wait until it finishes before running monthly refresh."
        )


def check_qdrant_is_available():
    print_section("Checking Qdrant")

    try:
        client = get_qdrant_client()
        collections = client.get_collections().collections
    except Exception as exc:
        raise RuntimeError(
            f"Qdrant is not reachable at {QDRANT_URL}. "
            f"Original error: {type(exc).__name__}: {exc}"
        ) from exc

    print("Qdrant is reachable.")
    print(f"Qdrant URL: {QDRANT_URL}")

    if collections:
        print("Existing collections:")
        for collection in collections:
            print(f"  - {collection.name}")
    else:
        print("No collections found yet.")


def extract_final_ingestion_result(status: dict[str, Any]) -> dict[str, Any]:
    """
    The admin endpoint can return slightly different shapes depending on
    ingestion_status implementation.
    """
    for key in ["result", "last_result", "data"]:
        value = status.get(key)
        if isinstance(value, dict):
            return value

    return status


def validate_ingestion_result(label: str, result: dict[str, Any]):
    """
    Important:
    The crawler may return status='success' even when pages failed and were
    placed inside skipped=[...]. So we check skipped/results too.
    """
    if result.get("status") == "error":
        raise RuntimeError(f"{label} ingestion failed: {result}")

    skipped = result.get("skipped") or []
    results = result.get("results") or []
    visited_count = int(result.get("visited_count") or 0)

    if skipped:
        raise RuntimeError(
            f"{label} ingestion completed with skipped/failed pages. "
            f"Skipped count={len(skipped)}. Details={skipped[:5]}"
        )

    if visited_count > 0 and not results:
        raise RuntimeError(
            f"{label} ingestion visited {visited_count} page(s), "
            f"but no page was successfully ingested. Result={result}"
        )

    print(f"{label} ingestion validation passed.")
    print(f"Visited pages: {visited_count}")
    print(f"Successfully ingested pages: {len(results)}")
    print(f"Skipped pages: {len(skipped)}")


def start_ingestion(label: str, payload: dict[str, Any]):
    print_section(f"Starting {label} Qdrant ingestion")

    print("Payload:")
    print(payload)

    result = request_json("POST", INGEST_URL_ENDPOINT, json=payload)

    print("Started ingestion:")
    print(result)


def wait_for_ingestion_to_finish(label: str, poll_seconds: int = POLL_SECONDS):
    print(f"\nWaiting for {label} ingestion to finish...")

    while True:
        status = request_json("GET", INGESTION_STATUS_ENDPOINT)

        print(status)

        if not status.get("active"):
            result = extract_final_ingestion_result(status)
            validate_ingestion_result(label, result)

            print(f"{label} ingestion finished successfully.")
            return result

        time.sleep(poll_seconds)


# ---------------------------------------------------------------------
# Qdrant cleanup
# ---------------------------------------------------------------------
def delete_collection_if_exists(collection_name: str):
    client = get_qdrant_client()

    try:
        if client.collection_exists(collection_name):
            client.delete_collection(collection_name)
            print(f"Deleted old Qdrant collection: {collection_name}")
        else:
            print(f"Collection does not exist, nothing to delete: {collection_name}")

    except Exception as exc:
        raise RuntimeError(
            f"Failed to delete Qdrant collection '{collection_name}'. "
            f"Qdrant URL: {QDRANT_URL}. "
            f"Original error: {type(exc).__name__}: {exc}"
        ) from exc


def clear_qdrant_collections(
    *,
    label: str,
    base_collection_name: str,
    delete_collection_name: str,
):
    """
    Deletes old Qdrant embeddings before monthly re-ingestion.

    Because the backend auto-adds '_docs', the real collection is usually:
      base: lifestore
      actual: lifestore_docs

    So this deletes the actual '_docs' collection. Optionally, it also deletes
    the base collection if it exists.
    """
    if not CLEAR_QDRANT_BEFORE_INGEST:
        print(f"Skipping Qdrant clear for {label}.")
        return

    print_section(f"Clearing old Qdrant embeddings for {label}")

    print(f"Base ingestion collection: {base_collection_name}")
    print(f"Actual delete collection: {delete_collection_name}")

    delete_collection_if_exists(delete_collection_name)

    if DELETE_BASE_COLLECTION_TOO and base_collection_name != delete_collection_name:
        delete_collection_if_exists(base_collection_name)


# ---------------------------------------------------------------------
# Qdrant ingestion jobs
# ---------------------------------------------------------------------
def run_lifestore_ingestion():
    clear_qdrant_collections(
        label="LifeStore",
        base_collection_name=LIFESTORE_COLLECTION_NAME,
        delete_collection_name=LIFESTORE_DELETE_COLLECTION_NAME,
    )

    payload = {
        "urls": [LIFESTORE_URL],
        "agent_name": LIFESTORE_AGENT_NAME,
        "collection_name": LIFESTORE_COLLECTION_NAME,
        "crawl": True,
        "max_pages": LIFESTORE_MAX_PAGES,
        "max_depth": LIFESTORE_MAX_DEPTH,
        "user_email": ADMIN_USER_EMAIL,
    }

    start_ingestion("LifeStore", payload)
    wait_for_ingestion_to_finish("LifeStore")


def run_slt_enterprise_ingestion():
    clear_qdrant_collections(
        label="SLT Enterprise",
        base_collection_name=ENTERPRISE_COLLECTION_NAME,
        delete_collection_name=ENTERPRISE_DELETE_COLLECTION_NAME,
    )

    payload = {
        "urls": [SLT_ENTERPRISE_URL],
        "agent_name": SLT_ENTERPRISE_AGENT_NAME,
        "collection_name": ENTERPRISE_COLLECTION_NAME,
        "crawl": True,
        "max_pages": ENTERPRISE_MAX_PAGES,
        "max_depth": ENTERPRISE_MAX_DEPTH,
        "user_email": ADMIN_USER_EMAIL,
    }

    start_ingestion("SLT Enterprise", payload)
    wait_for_ingestion_to_finish("SLT Enterprise")


# ---------------------------------------------------------------------
# Neo4j graph refresh
# ---------------------------------------------------------------------
def rebuild_lifestore_neo4j_graph():
    print_section("Rebuilding LifeStore Neo4j graph")

    script_path = ROOT_DIR / "backend" / "scripts" / "build_lifestore_graph.py"

    if not script_path.exists():
        raise RuntimeError(f"Neo4j graph script not found: {script_path}")

    result = subprocess.run(
        [sys.executable, str(script_path)],
        cwd=ROOT_DIR,
        check=False,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"Neo4j graph rebuild failed with exit code {result.returncode}"
        )

    print("LifeStore Neo4j graph rebuild finished successfully.")


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------
def main():
    require_env("ADMIN_USER_EMAIL", ADMIN_USER_EMAIL)

    print_section("Monthly KB refresh started")

    print(f"Admin base URL: {ADMIN_BASE_URL}")

    print(f"LifeStore agent: {LIFESTORE_AGENT_NAME}")
    print(f"LifeStore ingestion collection: {LIFESTORE_COLLECTION_NAME}")
    print(f"LifeStore delete collection: {LIFESTORE_DELETE_COLLECTION_NAME}")

    print(f"SLT Enterprise agent: {SLT_ENTERPRISE_AGENT_NAME}")
    print(f"SLT Enterprise ingestion collection: {ENTERPRISE_COLLECTION_NAME}")
    print(f"SLT Enterprise delete collection: {ENTERPRISE_DELETE_COLLECTION_NAME}")

    print(f"Clear Qdrant before ingest: {CLEAR_QDRANT_BEFORE_INGEST}")
    print(f"Delete base collection too: {DELETE_BASE_COLLECTION_TOO}")
    print(f"Run Qdrant ingestions: {RUN_QDRANT_INGESTIONS}")
    print(f"Run Neo4j graph refresh: {RUN_NEO4J_GRAPH_REFRESH}")

    check_backend_is_available()

    if RUN_QDRANT_INGESTIONS:
        check_qdrant_is_available()
        run_lifestore_ingestion()
        run_slt_enterprise_ingestion()
    else:
        print("Skipping Qdrant ingestions because RUN_QDRANT_INGESTIONS=false")

    if RUN_NEO4J_GRAPH_REFRESH:
        rebuild_lifestore_neo4j_graph()
    else:
        print("Skipping Neo4j graph refresh because RUN_NEO4J_GRAPH_REFRESH=false")

    print_section("Monthly KB refresh completed successfully")


if __name__ == "__main__":
    main()