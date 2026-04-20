"""
RAG tool for searching agent-specific Qdrant knowledge-base collections.

Each agent stores its documents in a dedicated Qdrant collection named
``<agent_id>_docs`` (e.g. ``hr_docs``, ``finance_docs``).  This tool
embeds the user query with Gemini, runs a similarity search, and returns
the top-k document chunks as a single concatenated context string.
"""

import logging
from typing import Annotated

from langchain_core.tools import tool
from langchain_qdrant import QdrantVectorStore, FastEmbedSparse, RetrievalMode
from langgraph.prebuilt import InjectedState
from qdrant_client import QdrantClient

from core.config import settings
from core.llm import get_embedding_model

log = logging.getLogger(__name__)

# Sparse embedding model instantiated once at import time - BM25 is
# lightweight and stateless, so a single shared instance is fine.
_sparse_embeddings = FastEmbedSparse(model_name="Qdrant/bm25")


@tool
async def search_knowledge_base(
    query: str,
    agent_id: Annotated[str, InjectedState("agent_id")],
) -> str:
    """Search the knowledge base for documents relevant to the user's query.

    Uses hybrid retrieval (dense semantic + BM25 lexical) to balance
    semantic understanding with exact-match recall on codes, IDs, and
    proper nouns.

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

        # Distinguish "collection does not exist" from "collection empty / no match".
        # Without this check, a missing collection surfaces as an ambiguous Qdrant
        # error that gets downgraded to "No relevant documents found." — the LLM
        # then treats it as a normal empty-result and is tempted to hallucinate.
        try:
            collection_present = client.collection_exists(collection_name)
        except Exception as probe_err:
            log.warning(
                f"Qdrant collection_exists probe failed for '{collection_name}': "
                f"{type(probe_err).__name__}: {probe_err}"
            )
            collection_present = True  # fall through to real search; it will error cleanly

        if not collection_present:
            log.error(
                f"Qdrant collection '{collection_name}' does not exist. "
                f"Agent '{agent_id}' has no knowledge base configured."
            )
            return "[KB_UNAVAILABLE] No knowledge base is configured for this agent."

        vector_store = QdrantVectorStore(
            client=client,
            collection_name=collection_name,
            embedding=embeddings,
            sparse_embedding=_sparse_embeddings,
            retrieval_mode=RetrievalMode.HYBRID,
            vector_name="dense",
            sparse_vector_name="sparse",
        )

        # --- Hybrid similarity search (top 15 chunks) --------------------
        # Higher k gives the LLM more recall; hybrid ranking keeps
        # precision acceptable without a separate reranker.
        results = await vector_store.asimilarity_search(query=query, k=15)

        if not results:
            log.info(f"Hybrid search returned 0 results for agent='{agent_id}' query='{query}'")
            return "No relevant documents found."

        # Include source metadata in the context
        context_parts = []
        for doc in results:
            source = doc.metadata.get("source", "Unknown Source")
            link = doc.metadata.get("link", "#")
            context_parts.append(f"[Source: {source} | Link: {link}]\n{doc.page_content}")

        return "\n\n---\n\n".join(context_parts)

    except Exception as e:
        # Log the real reason (collection missing, Qdrant down, sparse
        # model load failure, etc.) instead of silently swallowing it.
        log.exception(
            f"Qdrant hybrid search failed for agent='{agent_id}' "
            f"collection='{agent_id}_docs': {type(e).__name__}: {e}"
        )
        return "No relevant documents found."
