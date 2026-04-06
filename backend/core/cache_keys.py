"""
Chat response cache: key derivation, normalization, and safe-to-cache heuristics.
"""

from __future__ import annotations

import hashlib
import re

# Bump to invalidate all entries after prompt/KB major changes.
CACHE_VERSION = "v1"

# Agents that call user-specific APIs (e.g. leave balance) must not use shared cache.
AGENTS_SKIP_CACHE = frozenset({"hr", "askhr"})

# Obvious follow-ups / personalization — skip shared FAQ cache.
_SKIP_MESSAGE_PATTERNS = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bwhat did i\b",
        r"\bwhat i (said|told|asked)\b",
        r"\bearlier\b",
        r"\bbefore\b.*\b(said|told|mentioned)\b",
        r"\byou said\b",
        r"\bremember\b.*\b(i said|we discussed)\b",
        r"\bmy leave\b",
        r"\bmy balance\b",
        r"\bmy document\b",
        r"\bmy nic\b",
        r"\bmy employee\b",
        r"\bsummarize my\b",
    )
)


def normalize_question(q: str) -> str:
    return q.strip().lower()


def hash_question(q: str) -> str:
    return hashlib.sha256(q.encode("utf-8")).hexdigest()


def build_cache_key(agent_id: str, question: str, version: str = CACHE_VERSION) -> str:
    normalized = normalize_question(question)
    hashed = hash_question(normalized)
    return f"chat:{agent_id}:{version}:{hashed}"


def should_skip_cache_for_message(message: str) -> bool:
    """True if the message looks contextual / personal — do not use shared cache."""
    if not message or not message.strip():
        return True
    for pat in _SKIP_MESSAGE_PATTERNS:
        if pat.search(message):
            return True
    return False


def should_use_cache_for_agent(agent_id: str) -> bool:
    return agent_id.lower() not in AGENTS_SKIP_CACHE
