"""
Qdrant-backed semantic cache for chat responses.

Stores:
- embedding vector (MiniLM 384-d)
- payload: question, answer, agent_id

Query:
- embed incoming question
- Qdrant search (top 1) with agent_id filter
- if score > threshold (cosine similarity), return cached answer

Fail-open:
- Any Qdrant connectivity/schema error returns None (no cache hit).
"""

from __future__ import annotations

import hashlib
import logging
from typing import Optional

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from core.config import settings
from core.embeddings import embed_text, get_embedding_dimension

logger = logging.getLogger(__name__)

SEMANTIC_COLLECTION = "chat_semantic_cache"


def _point_id(agent_id: str, normalized_question: str) -> str:
    """
    Deterministic point id to de-dupe repeated inserts for same agent+question.
    """
    raw = f"{agent_id.lower()}::{normalized_question}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _get_qdrant_client() -> Optional[QdrantClient]:
    try:
        return QdrantClient(url=settings.QDRANT_URL)
    except Exception as exc:
        logger.warning("Qdrant client unavailable; skipping semantic cache: %s", exc)
        return None


def _ensure_collection(client: QdrantClient) -> bool:
    """
    Ensure the semantic cache collection exists with cosine distance.
    Returns True if ready, False otherwise.
    """
    try:
        existing = client.get_collections().collections
        if any(c.name == SEMANTIC_COLLECTION for c in existing):
            return True

        client.create_collection(
            collection_name=SEMANTIC_COLLECTION,
            vectors_config=qm.VectorParams(
                size=get_embedding_dimension(),
                distance=qm.Distance.COSINE,
            ),
        )
        return True
    except Exception as exc:
        logger.warning("Qdrant collection init failed; skipping semantic cache: %s", exc)
        return False


def semantic_lookup(
    *,
    agent_id: str,
    question: str,
    similarity_threshold: float = 0.85,
) -> Optional[str]:
    """
    Return cached answer if semantic match found (score > threshold), else None.
    """
    client = _get_qdrant_client()
    if client is None:
        return None
    if not _ensure_collection(client):
        return None

    vec = embed_text(question)
    if vec is None:
        return None

    try:
        hits = client.search(
            collection_name=SEMANTIC_COLLECTION,
            query_vector=vec,
            limit=1,
            with_payload=True,
            query_filter=qm.Filter(
                must=[
                    qm.FieldCondition(
                        key="agent_id",
                        match=qm.MatchValue(value=agent_id.lower()),
                    )
                ]
            ),
        )
        if not hits:
            return None

        hit = hits[0]
        score = float(hit.score or 0.0)
        if score < similarity_threshold:
            return None

        payload = hit.payload or {}
        ans = payload.get("answer")
        if isinstance(ans, str) and ans.strip():
            return ans
        return None
    except Exception as exc:
        logger.warning("Qdrant semantic search failed; continuing: %s", exc)
        return None


def semantic_store(
    *,
    agent_id: str,
    normalized_question: str,
    answer: str,
) -> None:
    """Upsert a semantic cache entry (best-effort)."""
    if not normalized_question or not normalized_question.strip():
        return
    if not answer or not answer.strip():
        return

    client = _get_qdrant_client()
    if client is None:
        return
    if not _ensure_collection(client):
        return

    vec = embed_text(normalized_question)
    if vec is None:
        return

    try:
        pid = _point_id(agent_id, normalized_question)
        client.upsert(
            collection_name=SEMANTIC_COLLECTION,
            points=[
                qm.PointStruct(
                    id=pid,
                    vector=vec,
                    payload={
                        "agent_id": agent_id.lower(),
                        "question": normalized_question,
                        "answer": answer,
                    },
                )
            ],
        )
    except Exception as exc:
        logger.warning("Qdrant semantic upsert failed; continuing: %s", exc)
