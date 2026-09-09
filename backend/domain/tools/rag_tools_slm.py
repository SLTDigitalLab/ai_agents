"""
RAG tool for the Ask HR SLM agent — queries the askhrslm_docs Qdrant
collection using Ollama embeddings (nomic-embed-text, 768d).
"""

import logging

from langchain_core.tools import tool
from langchain_qdrant import QdrantVectorStore, FastEmbedSparse, RetrievalMode
from qdrant_client import QdrantClient

from core.config import settings
from core.llm_slm import get_slm_embedding_model

log = logging.getLogger(__name__)

_sparse_embeddings = FastEmbedSparse(model_name="Qdrant/bm25")
_COLLECTION = "askhrslm_docs"


@tool
async def search_hr_slm_kb(query: str) -> str:
    """Search the HR knowledge base (internal SLM-embedded collection) for
    documents relevant to the user's question.

    Args:
        query: The user's natural-language question about HR policies,
               leave, benefits, or procedures.

    Returns:
        A concatenated string of the most relevant HR document chunks,
        or an informational message when no documents are found.
    """
    try:
        embeddings = get_slm_embedding_model()
        client = QdrantClient(url=settings.QDRANT_URL)

        try:
            present = client.collection_exists(_COLLECTION)
        except Exception as probe_err:
            log.warning(
                f"Qdrant probe failed for '{_COLLECTION}': "
                f"{type(probe_err).__name__}: {probe_err}"
            )
            present = True

        if not present:
            log.error(f"Collection '{_COLLECTION}' does not exist.")
            return "[KB_UNAVAILABLE] No HR knowledge base is configured yet."

        try:
            vector_store = QdrantVectorStore(
                client=client,
                collection_name=_COLLECTION,
                embedding=embeddings,
            )
            results = await vector_store.asimilarity_search(query=query, k=4)
        except Exception as dense_err:
            log.warning(f"Standard dense search failed, trying hybrid named vectors: {dense_err}")

            vector_store = QdrantVectorStore(
                client=client,
                collection_name=_COLLECTION,
                embedding=embeddings,
                sparse_embedding=_sparse_embeddings,
                retrieval_mode=RetrievalMode.HYBRID,
                vector_name="dense",
                sparse_vector_name="sparse",
            )

            results = await vector_store.asimilarity_search(query=query, k=4)
        if not results:
            return "No relevant documents found."

        context_parts = []
        for doc in results:
            source = (
                doc.metadata.get("source")
                or doc.metadata.get("filename")
                or doc.metadata.get("file_name")
                or "Unknown Source"
            )
            link = doc.metadata.get("link", "#")
            context_parts.append(f"[Source: {source} | Link: {link}]\n{doc.page_content}")

        return "\n\n---\n\n".join(context_parts)

    except Exception as e:
        log.exception(
            f"SLM hybrid search failed for collection='{_COLLECTION}': "
            f"{type(e).__name__}: {e}"
        )
        return "No relevant documents found."
