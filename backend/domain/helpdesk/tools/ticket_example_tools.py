"""
Retrieval over REAL historical tickets — an alternative reference corpus
to category_kb_tools.py's 75 hand-written KB rows. Same hybrid Qdrant
search pattern, pointed at real past tickets ingested by
domain/helpdesk/scripts/ingest_ticket_examples.py.
"""

import logging

from langchain_qdrant import QdrantVectorStore, FastEmbedSparse, RetrievalMode
from qdrant_client import QdrantClient

from core.config import settings
from core.llm import get_embedding_model

log = logging.getLogger(__name__)

TICKET_EXAMPLES_COLLECTION = "helpdesk_ticket_examples_docs"

_sparse_embeddings = FastEmbedSparse(model_name="Qdrant/bm25")


async def search_ticket_examples(query: str, k: int = 5) -> list[dict]:
    """Hybrid-search TICKET_EXAMPLES_COLLECTION and return the top-k most
    similar real historical tickets, each as a dict with main_category,
    sub_category, example_text, and relevance score. Returns an empty list
    (never raises) if the collection is missing or the search fails."""
    try:
        client = QdrantClient(url=settings.QDRANT_URL)

        try:
            collection_present = client.collection_exists(TICKET_EXAMPLES_COLLECTION)
        except Exception as probe_err:
            log.warning(
                "Qdrant collection_exists probe failed for '%s': %s: %s",
                TICKET_EXAMPLES_COLLECTION,
                type(probe_err).__name__,
                probe_err,
            )
            collection_present = True

        if not collection_present:
            log.error(
                "Qdrant collection '%s' does not exist — run "
                "domain/helpdesk/scripts/ingest_ticket_examples.py first.",
                TICKET_EXAMPLES_COLLECTION,
            )
            return []

        vector_store = QdrantVectorStore(
            client=client,
            collection_name=TICKET_EXAMPLES_COLLECTION,
            embedding=get_embedding_model(),
            sparse_embedding=_sparse_embeddings,
            retrieval_mode=RetrievalMode.HYBRID,
            vector_name="dense",
            sparse_vector_name="sparse",
        )

        results = await vector_store.asimilarity_search_with_score(query=query, k=k)

        examples = []
        for doc, score in results:
            example = dict(doc.metadata)
            example["example_text"] = doc.page_content
            example["_score"] = score
            examples.append(example)

        log.info(
            "Ticket-example search returned %d example(s) for query=%r",
            len(examples),
            query,
        )
        return examples

    except Exception as exc:
        log.exception(
            "Ticket-example hybrid search failed for query=%r: %s: %s",
            query,
            type(exc).__name__,
            exc,
        )
        return []
