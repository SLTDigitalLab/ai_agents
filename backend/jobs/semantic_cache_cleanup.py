"""
Semantic cache cleanup for Qdrant.

Why this exists:
- Redis caches expire (TTL), but Qdrant points do not.
- Without explicit expiry, semantic cache can serve stale answers after Redis has
  correctly expired them.

Safety:
- Semantic lookup enforces expiry at read-time (no stale reads).
- This job is best-effort housekeeping to keep the Qdrant collection small.
"""

from __future__ import annotations

import logging
import time
from typing import Iterable, Optional

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from core.config import settings
from core.semantic_cache import SEMANTIC_COLLECTION

logger = logging.getLogger(__name__)


def _get_client() -> Optional[QdrantClient]:
    try:
        return QdrantClient(url=settings.QDRANT_URL)
    except Exception as exc:
        logger.warning("Qdrant client unavailable; skipping semantic cleanup: %s", exc)
        return None


def _collection_exists(client: QdrantClient) -> bool:
    try:
        cols = client.get_collections().collections
        return any(c.name == SEMANTIC_COLLECTION for c in cols)
    except Exception as exc:
        logger.warning("Qdrant collection list failed; skipping cleanup: %s", exc)
        return False


def _chunked(it: Iterable, size: int) -> Iterable[list]:
    buf: list = []
    for x in it:
        buf.append(x)
        if len(buf) >= size:
            yield buf
            buf = []
    if buf:
        yield buf


def cleanup_expired_semantic_cache(
    *,
    now: Optional[int] = None,
    scroll_page_size: int = 256,
    delete_batch_size: int = 256,
) -> int:
    """
    Delete expired semantic cache points from Qdrant.

    - Uses Qdrant "scroll" with a filter (expires_at < now) to avoid scanning the
      entire collection client-side.
    - Deletes in batches by point id.

    Returns number of deleted points (best-effort; may be lower if Qdrant errors).
    """
    ts = int(time.time()) if now is None else int(now)
    client = _get_client()
    if client is None:
        return 0
    if not _collection_exists(client):
        return 0

    expired_filter = qm.Filter(
        must=[
            qm.FieldCondition(
                key="expires_at",
                range=qm.Range(lt=ts),
            )
        ]
    )

    deleted = 0
    next_offset = None

    while True:
        try:
            points, next_offset = client.scroll(
                collection_name=SEMANTIC_COLLECTION,
                scroll_filter=expired_filter,
                limit=scroll_page_size,
                with_payload=False,
                with_vectors=False,
                offset=next_offset,
            )
        except Exception as exc:
            logger.warning("Qdrant scroll failed; stopping cleanup: %s", exc)
            break

        if not points:
            break

        ids = [p.id for p in points if p.id is not None]
        for batch in _chunked(ids, delete_batch_size):
            try:
                client.delete(
                    collection_name=SEMANTIC_COLLECTION,
                    points_selector=qm.PointIdsList(points=batch),
                )
                deleted += len(batch)
            except Exception as exc:
                logger.warning("Qdrant delete failed; continuing: %s", exc)

    return deleted


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    deleted = cleanup_expired_semantic_cache()
    logger.info("Semantic cache cleanup complete. deleted=%s", deleted)


if __name__ == "__main__":
    main()

