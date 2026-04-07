"""
Fuzzy matching layer for chat response caching.

Design goals:
- Very low latency (small bounded scan of recent cached questions).
- Safe: never bypass agent/message/thread cache safety gates (handled by caller).
- Fail-open: if Redis/RapidFuzz is unavailable or list entries are malformed,
  return no match and let the normal LLM flow proceed.

Storage:
- A global Redis list stores historical normalized questions:
    key: "cache_questions:list"
    value: JSON string {"agent_id": "<id>", "q": "<normalized_question>"}
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

FUZZY_QUESTIONS_LIST_KEY = "cache_questions:list"


@dataclass(frozen=True)
class FuzzyMatch:
    agent_id: str
    question: str  # normalized question from cache list
    score: float


def _safe_json_loads(s: str) -> Optional[dict]:
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def extract_questions_for_agent(
    raw_items: Iterable[str],
    agent_id: str,
) -> list[str]:
    """Extract normalized questions for a given agent from stored list items."""
    out: list[str] = []
    for item in raw_items:
        obj = _safe_json_loads(item)
        if not obj:
            continue
        if (obj.get("agent_id") or "").lower() != agent_id.lower():
            continue
        q = obj.get("q")
        if isinstance(q, str) and q.strip():
            out.append(q)
    return out


def find_best_fuzzy_match(
    normalized_question: str,
    candidates: list[str],
    threshold: float = 90.0,
) -> Optional[tuple[str, float]]:
    """
    Return (best_candidate, score) if >= threshold, else None.

    Uses RapidFuzz token-based scoring for better robustness on small wording changes.
    """
    if not normalized_question or not candidates:
        return None

    try:
        from rapidfuzz import fuzz, process  # type: ignore
    except Exception as exc:
        logger.warning("RapidFuzz unavailable; skipping fuzzy cache: %s", exc)
        return None

    # token_sort_ratio handles word-order differences with good speed.
    best = process.extractOne(
        normalized_question,
        candidates,
        scorer=fuzz.token_sort_ratio,
    )
    if not best:
        return None

    match, score, _idx = best
    if score >= threshold:
        return str(match), float(score)
    return None


def append_question_to_redis_list(
    redis_client,
    agent_id: str,
    normalized_question: str,
    list_key: str = FUZZY_QUESTIONS_LIST_KEY,
    max_list_size: int = 5000,
) -> None:
    """
    Append (agent_id, normalized_question) to the Redis list and trim size.

    Uses RPUSH so list order is oldest->newest; callers can scan from the tail
    (most recent) if desired.
    """
    if redis_client is None:
        return
    if not normalized_question or not normalized_question.strip():
        return

    payload = json.dumps({"agent_id": agent_id, "q": normalized_question})
    try:
        pipe = redis_client.pipeline()
        pipe.rpush(list_key, payload)
        # Keep only the most recent max_list_size items.
        pipe.ltrim(list_key, -max_list_size, -1)
        pipe.execute()
    except Exception as exc:
        logger.warning("Redis fuzzy list append failed; continuing: %s", exc)


def fuzzy_lookup_from_redis(
    redis_client,
    agent_id: str,
    normalized_question: str,
    *,
    list_key: str = FUZZY_QUESTIONS_LIST_KEY,
    scan_limit: int = 500,
    threshold: float = 90.0,
) -> Optional[FuzzyMatch]:
    """
    Look for a fuzzy match in the Redis questions list.

    - Reads up to `scan_limit` most recent items (bounded).
    - Filters candidates by agent_id.
    - Returns the best match if above threshold.
    """
    if redis_client is None:
        return None
    if not normalized_question or not normalized_question.strip():
        return None

    try:
        # Read most recent items to bias toward fresh KB/prompt behavior.
        raw = redis_client.lrange(list_key, -scan_limit, -1)
    except Exception as exc:
        logger.warning("Redis LRANGE failed; skipping fuzzy cache: %s", exc)
        return None

    candidates = extract_questions_for_agent(raw_items=raw, agent_id=agent_id)
    best = find_best_fuzzy_match(
        normalized_question=normalized_question,
        candidates=candidates,
        threshold=threshold,
    )
    if not best:
        return None

    q, score = best
    return FuzzyMatch(agent_id=agent_id, question=q, score=score)
