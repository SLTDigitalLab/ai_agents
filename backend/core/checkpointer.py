"""
Schema-isolated checkpointers backed by long-lived connection pools.

Each agent gets its own PostgreSQL schema (e.g. ``agent_askhr``) for data
isolation. Rather than opening a fresh pool + running ``setup()`` on every
request (expensive against a remote Postgres), we lazily create **one
long-lived pool and one checkpointer per agent** and reuse them for the
lifetime of the process. ``setup()`` (schema + table migrations) runs once,
the first time an agent is touched.

The pools are managed by ``psycopg_pool`` which transparently re-establishes
broken connections (e.g. after a DB restart); ``check=check_connection``
validates each connection on checkout so a stale one is never handed out.

Accessors:
- **Sync** (``get_sync_checkpointer``): non-streaming endpoints (history, admin).
- **Async** (``get_async_checkpointer``): streaming endpoints (``astream_events``).

Call ``close_sync_pools()`` / ``aclose_async_pools()`` on application shutdown.

The legacy per-request context managers (``get_postgres_checkpointer`` /
``get_async_postgres_checkpointer``) are kept for backward compatibility but
are no longer used by the routers.
"""

import re
import asyncio
import logging
import threading
from contextlib import contextmanager, asynccontextmanager

import psycopg
from psycopg_pool import ConnectionPool, AsyncConnectionPool
from psycopg.rows import dict_row
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from core.config import settings

logger = logging.getLogger(__name__)

# Pool sizing (per agent schema).
_POOL_MIN_SIZE = 1
_POOL_MAX_SIZE = 10


def _sanitize_schema_name(agent_id: str) -> str:
    """Turn an arbitrary agent_id into a safe PostgreSQL schema name."""
    clean = re.sub(r"[^a-z0-9_]", "_", agent_id.lower())
    return f"agent_{clean}"


def _conninfo(schema: str) -> str:
    """Connection string that pins the per-agent schema via search_path."""
    return f"{settings.POSTGRES_URL}?options=-csearch_path%3D{schema}"


def _pool_kwargs() -> dict:
    return {
        "autocommit": True,
        "prepare_threshold": 0,
        "row_factory": dict_row,
    }


def _ensure_schema(schema: str):
    """Create the PostgreSQL schema if it doesn't already exist."""
    try:
        with psycopg.connect(settings.POSTGRES_URL, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    except Exception as e:
        print(f"WARNING: Schema creation check failed (might already exist or connection error): {e}")


# ── Cached, long-lived checkpointers ──────────────────────────────────────

_sync_pools: dict[str, ConnectionPool] = {}
_sync_checkpointers: dict[str, PostgresSaver] = {}
_sync_lock = threading.Lock()

_async_pools: dict[str, AsyncConnectionPool] = {}
_async_checkpointers: dict[str, AsyncPostgresSaver] = {}
_async_lock = asyncio.Lock()


def get_sync_checkpointer(agent_id: str) -> PostgresSaver:
    """Return a cached ``PostgresSaver`` for the agent, creating it on first use.

    The underlying pool stays open for the process lifetime. Thread-safe
    (sync endpoints run in FastAPI's threadpool).
    """
    cp = _sync_checkpointers.get(agent_id)
    if cp is not None:
        return cp

    with _sync_lock:
        cp = _sync_checkpointers.get(agent_id)
        if cp is not None:
            return cp

        schema = _sanitize_schema_name(agent_id)
        _ensure_schema(schema)

        pool = ConnectionPool(
            conninfo=_conninfo(schema),
            min_size=_POOL_MIN_SIZE,
            max_size=_POOL_MAX_SIZE,
            open=False,
            check=ConnectionPool.check_connection,
            kwargs=_pool_kwargs(),
        )
        pool.open()
        checkpointer = PostgresSaver(pool)
        checkpointer.setup()

        _sync_pools[agent_id] = pool
        _sync_checkpointers[agent_id] = checkpointer
        logger.info("Initialized sync checkpointer pool for agent %r", agent_id)
        return checkpointer


async def get_async_checkpointer(agent_id: str) -> AsyncPostgresSaver:
    """Return a cached ``AsyncPostgresSaver`` for the agent, creating it on first use.

    The underlying pool stays open for the process lifetime. Concurrency-safe
    via an asyncio lock; the cached checkpointer is reused across all requests.
    """
    cp = _async_checkpointers.get(agent_id)
    if cp is not None:
        return cp

    async with _async_lock:
        cp = _async_checkpointers.get(agent_id)
        if cp is not None:
            return cp

        schema = _sanitize_schema_name(agent_id)
        _ensure_schema(schema)

        pool = AsyncConnectionPool(
            conninfo=_conninfo(schema),
            min_size=_POOL_MIN_SIZE,
            max_size=_POOL_MAX_SIZE,
            open=False,
            check=AsyncConnectionPool.check_connection,
            kwargs=_pool_kwargs(),
        )
        await pool.open()
        checkpointer = AsyncPostgresSaver(pool)
        await checkpointer.setup()

        _async_pools[agent_id] = pool
        _async_checkpointers[agent_id] = checkpointer
        logger.info("Initialized async checkpointer pool for agent %r", agent_id)
        return checkpointer


def close_sync_pools():
    """Close all cached sync pools (call on application shutdown)."""
    for agent_id, pool in list(_sync_pools.items()):
        try:
            pool.close()
        except Exception as e:
            logger.warning("Error closing sync pool for %r: %s", agent_id, e)
    _sync_pools.clear()
    _sync_checkpointers.clear()


async def aclose_async_pools():
    """Close all cached async pools (call on application shutdown)."""
    for agent_id, pool in list(_async_pools.items()):
        try:
            await pool.close()
        except Exception as e:
            logger.warning("Error closing async pool for %r: %s", agent_id, e)
    _async_pools.clear()
    _async_checkpointers.clear()


# ── Legacy per-request context managers (kept for backward compatibility) ──

@contextmanager
def get_postgres_checkpointer(agent_id: str):
    """Deprecated: opens a fresh pool per call. Prefer ``get_sync_checkpointer``."""
    schema = _sanitize_schema_name(agent_id)
    _ensure_schema(schema)

    with ConnectionPool(
        conninfo=_conninfo(schema),
        min_size=1,
        max_size=10,
        kwargs=_pool_kwargs(),
    ) as pool:
        checkpointer = PostgresSaver(pool)
        checkpointer.setup()
        yield checkpointer


@asynccontextmanager
async def get_async_postgres_checkpointer(agent_id: str):
    """Deprecated: opens a fresh pool per call. Prefer ``get_async_checkpointer``."""
    schema = _sanitize_schema_name(agent_id)
    _ensure_schema(schema)

    async with AsyncConnectionPool(
        conninfo=_conninfo(schema),
        min_size=1,
        max_size=10,
        kwargs=_pool_kwargs(),
    ) as pool:
        checkpointer = AsyncPostgresSaver(pool)
        await checkpointer.setup()
        yield checkpointer
