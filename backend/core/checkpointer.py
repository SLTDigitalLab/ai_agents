"""
Checkpointer context manager – creates schema-isolated PostgresSaver instances per request.

Each agent gets its own PostgreSQL schema (e.g. ``agent_askhr``) for data isolation.
Usage employs a context manager to ensure the connection pool is cleanly closed
after each request, preventing "RuntimeError: cannot join current thread".

Usage::

    from core.checkpointer import get_postgres_checkpointer

    # In your API router:
    with get_postgres_checkpointer("askhr") as checkpointer:
        graph = builder.compile(checkpointer=checkpointer)
        result = graph.invoke(...)
    # Pool closes here automatically
"""

import re
from contextlib import contextmanager

import psycopg
from psycopg_pool import ConnectionPool
from psycopg.rows import dict_row
from langgraph.checkpoint.postgres import PostgresSaver

from core.config import settings


def _sanitize_schema_name(agent_id: str) -> str:
    """Turn an arbitrary agent_id into a safe PostgreSQL schema name."""
    clean = re.sub(r"[^a-z0-9_]", "_", agent_id.lower())
    return f"agent_{clean}"


@contextmanager
def get_postgres_checkpointer(agent_id: str):
    """Context manager yielding a schema-isolated ``PostgresSaver``.

    Lifecycle:
    1. Opens a ``ConnectionPool`` (configures ``search_path``).
    2. Ensures schema exists (if not already).
    3. Yields a configured ``PostgresSaver``.
    4. Closes the pool on exit.
    """
    schema = _sanitize_schema_name(agent_id)

    # 1. Ensure schema exists using a direct ephemeral connection (no pool needed)
    try:
        # Use autocommit=True so the CREATE SCHEMA statement runs immediately
        with psycopg.connect(settings.POSTGRES_URL, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    except Exception as e:
        print(f"WARNING: Schema creation check failed (might already exist or connection error): {e}")

    # 2. Connection string forcing the schema search_path
    conninfo = (
        f"{settings.POSTGRES_URL}"
        f"?options=-csearch_path%3D{schema}"
    )

    # 3. Create the main pool for the checkpointer
    # We use a context manager here so it closes automatically
    with ConnectionPool(
        conninfo=conninfo,
        min_size=1,   # Start with 1 connection
        max_size=10,  # Allow up to 10
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
        },
    ) as pool:
        
        # 4. Create the saver and run setup (creates tables if missing)
        checkpointer = PostgresSaver(pool)
        checkpointer.setup()

        yield checkpointer
