"""
Lazy Redis client for response caching.

If ``REDIS_URL`` is unset or Redis is unreachable, helpers return ``None`` so
the chat flow continues without caching.
"""

from __future__ import annotations

import logging
from typing import Optional

import redis

from core.config import settings

logger = logging.getLogger(__name__)

_client: Optional[redis.Redis] = None
_client_failed: bool = False


def get_redis_client() -> Optional[redis.Redis]:
    """Return a shared ``redis.Redis`` client, or ``None`` if caching is disabled."""
    global _client, _client_failed

    if not settings.REDIS_URL:
        return None
    if _client_failed:
        return None
    if _client is not None:
        return _client

    try:
        _client = redis.Redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
        )
        _client.ping()
        return _client
    except Exception as exc:
        logger.warning("Redis unavailable; continuing without cache: %s", exc)
        _client_failed = True
        _client = None
        return None


def reset_client_for_tests() -> None:
    """Reset module state (tests only)."""
    global _client, _client_failed
    _client = None
    _client_failed = False
