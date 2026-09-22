"""
Vector-based retrieval over the helpdesk category knowledge base — a fixed
Qdrant collection of 75 category/subcategory rows (ingested via
domain/helpdesk/scripts/ingest_category_kb.py), used to find the top-k
categories closest to a ticket message.
"""

import logging

from langchain_qdrant import QdrantVectorStore, FastEmbedSparse, RetrievalMode
from qdrant_client import QdrantClient

from core.config import settings
from core.llm import get_embedding_model

log = logging.getLogger(__name__)

CATEGORY_KB_COLLECTION = "helpdesk_category_kb_docs"

_sparse_embeddings = FastEmbedSparse(model_name="Qdrant/bm25")


async def search_category_candidates(query: str, k: int = 5) -> list[dict]:
    """Hybrid-search CATEGORY_KB_COLLECTION and return the top-k category
    rows (each carrying its full ingested metadata + relevance score).
    Returns an empty list (never raises) on any failure."""
    try:
        client = QdrantClient(url=settings.QDRANT_URL)

        try:
            collection_present = client.collection_exists(CATEGORY_KB_COLLECTION)
        except Exception as probe_err:
            log.warning(
                "Qdrant collection_exists probe failed for '%s': %s: %s",
                CATEGORY_KB_COLLECTION,
                type(probe_err).__name__,
                probe_err,
            )
            collection_present = True

        if not collection_present:
            log.error(
                "Qdrant collection '%s' does not exist — run "
                "domain/helpdesk/scripts/ingest_category_kb.py first.",
                CATEGORY_KB_COLLECTION,
            )
            return []

        vector_store = QdrantVectorStore(
            client=client,
            collection_name=CATEGORY_KB_COLLECTION,
            embedding=get_embedding_model(),
            sparse_embedding=_sparse_embeddings,
            retrieval_mode=RetrievalMode.HYBRID,
            vector_name="dense",
            sparse_vector_name="sparse",
        )

        results = await vector_store.asimilarity_search_with_score(query=query, k=k)

        candidates = []
        for doc, score in results:
            candidate = dict(doc.metadata)
            candidate["_score"] = score
            candidates.append(candidate)

        log.info(
            "Category KB search returned %d candidate(s) for query=%r",
            len(candidates),
            query,
        )
        return candidates

    except Exception as exc:
        log.exception(
            "Category KB hybrid search failed for query=%r: %s: %s",
            query,
            type(exc).__name__,
            exc,
        )
        return []
