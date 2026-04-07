"""
Local embedding helper for semantic cache.

Requirements:
- sentence-transformers using model: sentence-transformers/all-MiniLM-L6-v2
- Output dimension: 384

Design:
- Lazy, process-wide singleton model to avoid repeated heavy loads.
- Fail-open: if the dependency/model cannot be loaded, callers should treat
  embeddings as unavailable and skip semantic caching.
"""

from __future__ import annotations

import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
_MODEL = None
_MODEL_FAILED = False


def get_embedding_dimension() -> int:
    # all-MiniLM-L6-v2 is fixed at 384 dims.
    return 384


def _load_model():
    global _MODEL, _MODEL_FAILED
    if _MODEL_FAILED:
        return None
    if _MODEL is not None:
        return _MODEL

    try:
        from sentence_transformers import SentenceTransformer  # type: ignore

        # Use CPU by default. If your infra has GPU, ST will typically auto-detect,
        # but we keep behavior conservative to avoid surprise GPU memory pressure.
        _MODEL = SentenceTransformer(_MODEL_NAME)
        return _MODEL
    except Exception as exc:
        logger.warning("Semantic cache embeddings unavailable: %s", exc)
        _MODEL_FAILED = True
        _MODEL = None
        return None


def embed_text(text: str) -> Optional[List[float]]:
    """Return a single embedding vector, or None if embeddings unavailable."""
    model = _load_model()
    if model is None:
        return None
    if not text or not text.strip():
        return None

    try:
        # normalize_embeddings=True makes cosine similarity equivalent to dot product,
        # and improves score stability for thresholding.
        vec = model.encode(
            [text],
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )[0]
        return vec.astype("float32").tolist()
    except Exception as exc:
        logger.warning("Embedding generation failed; skipping semantic cache: %s", exc)
        return None
