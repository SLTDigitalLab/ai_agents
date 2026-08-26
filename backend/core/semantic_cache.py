"""
semantic_cache.py — Redis-backed semantic cache for the AI chat system.

## How it works

Every cached entry stores:
  - The original question text (for debugging/logging)
  - The OpenAI embedding vector of the question
  - The full LLM answer text

On a cache **GET**:
  1. Embed the incoming question.
  2. Fetch all cached entry keys for the agent namespace.
  3. Compute cosine similarity between incoming vector and each cached vector.
  4. If max similarity >= threshold → return the cached answer (HIT).
  5. Otherwise → return None (MISS, caller must run the LLM).

On a cache **SET**:
  1. Embed the question.
  2. Store a Redis hash under:
       cache:{agent_id}:{uuid4}
     containing: question, answer, embedding (JSON).
  3. Register the key in a Redis SET:
       cache:{agent_id}:index
     so GET can enumerate all keys without a SCAN.
  4. Set TTL on both the hash and the index entry.

## Redis key layout

  cache:{agent_id}:index          → Redis SET of all entry keys for the agent
  cache:{agent_id}:{uuid}         → Redis HASH
      question  → str
      answer    → str
      embedding → JSON list[float]

## Performance note

For workloads with thousands of cached entries per agent a dedicated vector
store (e.g. Redis Stack with RediSearch) would be faster. The current
implementation loads all embeddings into memory on each GET — appropriate for
up to ~5 000 entries per agent (each vector is 1 536 floats × 4 bytes = ~6 KB,
so 5 000 entries ≈ 30 MB in memory, well within typical Python process limits).
If you need to scale beyond that, swap the brute-force loop for a Redis VECTOR
SET command (Redis Stack ≥7.2).
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Optional

import numpy as np
import redis.asyncio as aioredis

from core.cache_embedder import embed_text_for_cache
from core.config import settings

logger = logging.getLogger(__name__)

# ── Module-level singleton ───────────────────────────────────────────────────

_redis_client: Optional[aioredis.Redis] = None


async def _get_redis() -> aioredis.Redis:
    """Return the module-level async Redis client, creating it on first call."""
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )
        # Verify connectivity on first use so misconfiguration surfaces early.
        try:
            await _redis_client.ping()
            logger.info("SemanticCache: Redis connection established → %s", settings.REDIS_URL)
        except Exception as exc:  # pragma: no cover
            logger.warning(
                "SemanticCache: Redis unavailable (%s). Cache will be bypassed.", exc
            )
            _redis_client = None
            raise
    return _redis_client


# ── Internal helpers ─────────────────────────────────────────────────────────

def _index_key(agent_id: str) -> str:
    """Redis SET key that holds all entry keys for an agent's cache."""
    return f"cache:{agent_id}:index"


def _entry_key(agent_id: str, entry_id: str) -> str:
    """Redis HASH key for a single cached Q&A entry."""
    return f"cache:{agent_id}:{entry_id}"


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two equal-length vectors."""
    va = np.array(a, dtype=np.float32)
    vb = np.array(b, dtype=np.float32)
    denom = np.linalg.norm(va) * np.linalg.norm(vb)
    if denom == 0.0:
        return 0.0
    return float(np.dot(va, vb) / denom)


def _is_agent_exempt(agent_id: str) -> bool:
    """Return True if this agent's responses must never be cached."""
    exempt = {
        a.strip().lower()
        for a in settings.SEMANTIC_CACHE_EXEMPT_AGENTS.split(",")
        if a.strip()
    }
    return agent_id.lower() in exempt


# ── Public API ───────────────────────────────────────────────────────────────

async def cache_get(question: str, agent_id: str) -> Optional[str]:
    """
    Look up a semantically similar cached question for *agent_id*.

    Args:
        question:  The incoming user question (after PII masking).
        agent_id:  The agent handling the request (used as cache namespace).

    Returns:
        The cached answer string if a similar question was found,
        or ``None`` if no match (cache miss).
    """
    if not settings.SEMANTIC_CACHE_ENABLED:
        return None

    if _is_agent_exempt(agent_id):
        logger.debug("SemanticCache: agent '%s' is exempt — skipping GET.", agent_id)
        return None

    try:
        redis = await _get_redis()
    except Exception:
        return None  # Redis unavailable — degrade gracefully

    # Fetch the set of entry keys registered for this agent.
    index_key = _index_key(agent_id)
    entry_keys: set[str] = await redis.smembers(index_key)  # type: ignore[assignment]

    if not entry_keys:
        logger.debug("SemanticCache MISS (empty index) | agent=%s", agent_id)
        return None

    # Embed the incoming question.
    try:
        query_vec = await embed_text_for_cache(question)
    except Exception as exc:
        logger.warning("SemanticCache: embedding failed during GET (%s) — bypass.", exc)
        return None

    # Scan all cached entries and find the best match.
    best_similarity = 0.0
    best_answer: Optional[str] = None
    best_question: Optional[str] = None

    # Batch-fetch all entry hashes in a pipeline for speed.
    pipe = redis.pipeline()
    ordered_keys = list(entry_keys)
    for key in ordered_keys:
        pipe.hgetall(key)
    results = await pipe.execute()

    for key, entry in zip(ordered_keys, results):
        if not entry:
            continue  # Stale key (TTL expired but index not cleaned up yet)

        try:
            cached_vec: list[float] = json.loads(entry["embedding"])
        except (KeyError, json.JSONDecodeError, TypeError):
            logger.debug("SemanticCache: corrupt entry skipped | key=%s", key)
            continue

        sim = _cosine_similarity(query_vec, cached_vec)

        if sim > best_similarity:
            best_similarity = sim
            best_answer = entry.get("answer")
            best_question = entry.get("question")

    threshold = settings.SEMANTIC_CACHE_THRESHOLD

    if best_similarity >= threshold and best_answer:
        logger.info(
            "SemanticCache HIT | agent=%s | similarity=%.4f | "
            "cached_q=%r | incoming_q=%r",
            agent_id,
            best_similarity,
            best_question,
            question,
        )
        return best_answer

    logger.info(
        "SemanticCache MISS | agent=%s | best_similarity=%.4f (threshold=%.2f)",
        agent_id,
        best_similarity,
        threshold,
    )
    return None


async def cache_set(question: str, answer: str, agent_id: str) -> None:
    """
    Store a question → answer pair in the Redis semantic cache.

    Args:
        question:  The user question (after PII masking).
        answer:    The full LLM answer text.
        agent_id:  The agent that produced the answer (cache namespace).
    """
    if not settings.SEMANTIC_CACHE_ENABLED:
        return

    if _is_agent_exempt(agent_id):
        logger.debug("SemanticCache: agent '%s' is exempt — skipping SET.", agent_id)
        return

    # Don't cache empty or very short answers (likely errors/blocks).
    if not answer or len(answer.strip()) < 20:
        logger.debug(
            "SemanticCache: answer too short (%d chars) — skipping SET.", len(answer or "")
        )
        return

    try:
        redis = await _get_redis()
    except Exception:
        return  # Redis unavailable — degrade gracefully

    # Embed the question.
    try:
        question_vec = await embed_text_for_cache(question)
    except Exception as exc:
        logger.warning("SemanticCache: embedding failed during SET (%s) — skip.", exc)
        return

    entry_id = str(uuid.uuid4())
    entry_key = _entry_key(agent_id, entry_id)
    index_key = _index_key(agent_id)
    ttl = settings.SEMANTIC_CACHE_TTL

    entry_data = {
        "question": question,
        "answer": answer,
        "embedding": json.dumps(question_vec),
    }

    try:
        pipe = redis.pipeline()
        pipe.hset(entry_key, mapping=entry_data)
        pipe.expire(entry_key, ttl)
        # Register in the agent's index set.
        # NOTE: Index members don't expire automatically; stale keys are silently
        # skipped during GET (the hash lookup returns an empty dict after TTL).
        # For large-scale deployments, add a periodic cleanup job.
        pipe.sadd(index_key, entry_key)
        await pipe.execute()

        logger.info(
            "SemanticCache SET | agent=%s | entry_key=%s | ttl=%ds | q=%r",
            agent_id,
            entry_key,
            ttl,
            question,
        )
    except Exception as exc:
        logger.warning("SemanticCache: failed to store entry (%s).", exc)


async def cache_clear(agent_id: str) -> int:
    """
    Delete ALL cached entries for *agent_id*.

    Returns the number of entries deleted.
    This is useful for admin endpoints or when agent KB is re-ingested.
    """
    try:
        redis = await _get_redis()
    except Exception:
        return 0

    index_key = _index_key(agent_id)
    entry_keys: set[str] = await redis.smembers(index_key)  # type: ignore[assignment]

    if not entry_keys:
        return 0

    pipe = redis.pipeline()
    for key in entry_keys:
        pipe.delete(key)
    pipe.delete(index_key)
    await pipe.execute()

    logger.info("SemanticCache CLEARED | agent=%s | deleted=%d entries", agent_id, len(entry_keys))
    return len(entry_keys)


async def cache_stats(agent_id: str) -> dict:
    """
    Return basic stats about the cache for *agent_id*.

    Returns a dict with: agent_id, entry_count, redis_url.
    """
    try:
        redis = await _get_redis()
        index_key = _index_key(agent_id)
        count = await redis.scard(index_key)
        return {
            "agent_id": agent_id,
            "entry_count": count,
            "redis_url": settings.REDIS_URL,
            "enabled": settings.SEMANTIC_CACHE_ENABLED,
            "threshold": settings.SEMANTIC_CACHE_THRESHOLD,
            "ttl_seconds": settings.SEMANTIC_CACHE_TTL,
        }
    except Exception as exc:
        return {"agent_id": agent_id, "error": str(exc)}
