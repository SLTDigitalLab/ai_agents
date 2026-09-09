"""
Vector-based retrieval over the helpdesk category knowledge base.

Standalone from domain/tools/rag_tools.py: that module resolves a Qdrant
collection per agent_id (f"{agent_id}_docs") for general document search.
This module targets one fixed collection holding the 75 category/subcategory
rows from domain/helpdesk/data/Category_Knowledge_Base_v2.xlsx (ingested via
domain/helpdesk/scripts/ingest_category_kb.py), used to retrieve the top-k most
similar categories for a ticket message so an LLM can pick the best one from
just those candidates instead of the full category list.
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
    """Hybrid-search CATEGORY_KB_COLLECTION and return the top-k category rows.

    Each returned dict carries the row's full metadata (main_category,
    sub_category, intent, description, customer_expressions,
    internal_expressions, symptoms, keywords, similar_categories,
    do_not_use_when, representative_examples, common_resolution) as ingested
    by ingest_category_kb.py, plus the row's relevance score.

    Returns an empty list (never raises) if the collection is missing or the
    search fails, so callers can fall back gracefully.
    """
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
