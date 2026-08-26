"""
cache_embedder.py — Lightweight async embedding helper for the semantic cache.

Uses the same routing embedding model (text-embedding-3-small) already configured
for supervisor routing. This keeps cache lookups cheap and fast without touching
the main KB embedding model/collections.
"""

import logging
from functools import lru_cache

from openai import AsyncOpenAI

from core.config import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _get_openai_client() -> AsyncOpenAI:
    """Return a cached AsyncOpenAI client (created once per process)."""
    api_key = settings.ROUTING_EMBEDDING_API_KEY or settings.OPENAI_API_KEY
    return AsyncOpenAI(api_key=api_key)


async def embed_text_for_cache(text: str) -> list[float]:
    """
    Embed *text* using the routing embedding model and return the raw vector.

    The routing model (text-embedding-3-small, 1536-dim) is intentionally used
    here instead of the larger KB model — it is much cheaper and still accurate
    enough for short-text semantic matching (cache key comparison).

    Args:
        text: The question / query string to embed.

    Returns:
        A list of floats representing the embedding vector.

    Raises:
        RuntimeError: If the embedding provider is not 'openai' (only OpenAI
            routing embeddings are supported by the cache; extend here if needed).
    """
    provider = (settings.ROUTING_EMBEDDING_PROVIDER or "openai").lower().strip()

    if provider != "openai":
        raise RuntimeError(
            f"SemanticCache only supports 'openai' for routing embeddings; "
            f"got provider='{provider}'. Set ROUTING_EMBEDDING_PROVIDER=openai."
        )

    client = _get_openai_client()

    # Normalise whitespace — identical canonical form → identical embedding
    normalised = " ".join(text.strip().split())

    response = await client.embeddings.create(
        model=settings.ROUTING_EMBEDDING_MODEL,
        input=normalised,
    )

    return response.data[0].embedding
