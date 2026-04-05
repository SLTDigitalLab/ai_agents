"""
Checkpointer context manager – creates schema-isolated PostgresSaver instances per request.

Each agent gets its own PostgreSQL schema (e.g. ``agent_askhr``) for data isolation.
Provides both sync and async context managers:

- **Sync** (``get_postgres_checkpointer``): For non-streaming endpoints like history retrieval.
- **Async** (``get_async_postgres_checkpointer``): For streaming endpoints using ``astream_events``.

Usage::

    from core.checkpointer import get_postgres_checkpointer, get_async_postgres_checkpointer

    # Sync (e.g. history endpoint):
    with get_postgres_checkpointer("askhr") as checkpointer:
        graph = builder.compile(checkpointer=checkpointer)
        result = graph.invoke(...)

    # Async (e.g. streaming endpoint):
    async with get_async_postgres_checkpointer("askhr") as checkpointer:
        graph = builder.compile(checkpointer=checkpointer)
        async for event in graph.astream_events(...):
            ...
"""

import re
from contextlib import contextmanager, asynccontextmanager

import psycopg
from psycopg_pool import ConnectionPool, AsyncConnectionPool
from psycopg.rows import dict_row
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from core.config import settings


def _sanitize_schema_name(agent_id: str) -> str:
    """Turn an arbitrary agent_id into a safe PostgreSQL schema name."""
    clean = re.sub(r"[^a-z0-9_]", "_", agent_id.lower())
    return f"agent_{clean}"


def _ensure_schema(schema: str):
    """Create the PostgreSQL schema if it doesn't already exist."""
    try:
        with psycopg.connect(settings.POSTGRES_URL, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    except Exception as e:
        print(f"WARNING: Schema creation check failed (might already exist or connection error): {e}")


@contextmanager
def get_postgres_checkpointer(agent_id: str):
    """Sync context manager yielding a schema-isolated ``PostgresSaver``.

    Best for non-streaming endpoints (e.g. history retrieval, invoke).
    """
    schema = _sanitize_schema_name(agent_id)
    _ensure_schema(schema)

    conninfo = (
        f"{settings.POSTGRES_URL}"
        f"?options=-csearch_path%3D{schema}"
    )

    with ConnectionPool(
        conninfo=conninfo,
        min_size=1,
        max_size=10,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
        },
    ) as pool:
        checkpointer = PostgresSaver(pool)
        checkpointer.setup()
        yield checkpointer


@asynccontextmanager
async def get_async_postgres_checkpointer(agent_id: str):
    """Async context manager yielding a schema-isolated ``AsyncPostgresSaver``.

    Required for streaming endpoints that use ``astream_events``.
    """
    schema = _sanitize_schema_name(agent_id)
    _ensure_schema(schema)

    conninfo = (
        f"{settings.POSTGRES_URL}"
        f"?options=-csearch_path%3D{schema}"
    )

    async with AsyncConnectionPool(
        conninfo=conninfo,
        min_size=1,
        max_size=10,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
        },
    ) as pool:
        checkpointer = AsyncPostgresSaver(pool)
        await checkpointer.setup()
        yield checkpointer
