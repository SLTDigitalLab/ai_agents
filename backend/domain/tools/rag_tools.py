"""
RAG tool for searching agent-specific Qdrant knowledge-base collections.

Each agent stores its documents in a dedicated Qdrant collection named
``<agent_id>_docs`` (e.g. ``hr_docs``, ``finance_docs``).  This tool
embeds the user query with Gemini, runs a similarity search, reranks
with FlashRank for precision, and returns the top-k document chunks as
a single concatenated context string.
"""

import logging
from typing import Annotated

from flashrank import Ranker, RerankRequest
from langchain_core.tools import tool
from langchain_qdrant import QdrantVectorStore
from langgraph.prebuilt import InjectedState
from qdrant_client import QdrantClient

from core.config import settings
from core.llm import get_embedding_model

log = logging.getLogger(__name__)

# Initialize the FlashRank pairwise reranker once at module level
_ranker = Ranker(model_name="ms-marco-MiniLM-L-12-v2", cache_dir="/tmp/flashrank")
@tool
async def search_knowledge_base(
    query: str,
    agent_id: Annotated[str, InjectedState("agent_id")],
) -> str:
    """Search the knowledge base for documents relevant to the user's query.

    Args:
        query: The user's natural-language question.
        agent_id: Identifier for the target agent (e.g. "hr", "finance").
                  Determines the Qdrant collection searched.

    Returns:
        A concatenated string of the most relevant document chunks,
        or an informational message when no documents are found.
    """
    try:
        # --- Embeddings ---------------------------------------------------
        embeddings = get_embedding_model()

        # --- Qdrant client & collection ----------------------------------
        # We use the sync client here because LangChain QdrantVectorStore handles it,
        # but we call the async similarity search method.
        client = QdrantClient(url=settings.QDRANT_URL)
        collection_name = f"{agent_id}_docs"

        vector_store = QdrantVectorStore(
            client=client,
            collection_name=collection_name,
            embedding=embeddings,
        )

        # --- Async Similarity search (retrieve more, then rerank) ---------------
        results = await vector_store.asimilarity_search(query=query, k=15)

        if not results:
            return "No relevant documents found."

        log.info("=== RAG RETRIEVAL: query='%s' | %d chunks from Qdrant ===", query, len(results))
        for i, doc in enumerate(results):
            source = doc.metadata.get("source", "?")
            log.info("  [%d] %s | %s", i, source, doc.page_content.replace("\n", " "))

        # --- FlashRank pairwise reranking ------------------------------------
        # Build passages for the reranker, preserving original doc references
        rerank_passages = [
            {"id": i, "text": doc.page_content}
            for i, doc in enumerate(results)
        ]
        rerank_request = RerankRequest(query=query, passages=rerank_passages)
        ranked = _ranker.rerank(rerank_request)

        log.info("=== RERANKED (top 5) ===")
        for r in ranked[:5]:
            doc = results[r["id"]]
            source = doc.metadata.get("source", "?")
            log.info("  [orig=%d] score=%.4f | %s | %s", r["id"], r["score"], source, doc.page_content.replace("\n", " "))

        # Take top 5 reranked results, map back to original docs
        top_docs = [results[r["id"]] for r in ranked[:5]]

        # Include source metadata in the context
        context_parts = []
        for doc in top_docs:
            source = doc.metadata.get("source", "Unknown Source")
            link = doc.metadata.get("link", "#")
            context_parts.append(f"[Source: {source} | Link: {link}]\n{doc.page_content}")

        return "\n\n---\n\n".join(context_parts)

    except Exception as e:
        # Collection may not exist yet, or Qdrant may be unreachable
        print(f"DEBUG: Qdrant Search Error: {e}")
        return "No relevant documents found."
