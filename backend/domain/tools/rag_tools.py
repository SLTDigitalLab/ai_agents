"""
RAG tool for searching agent-specific Qdrant knowledge-base collections.

Each agent stores its documents in a dedicated Qdrant collection named
``<agent_id>_docs`` (e.g. ``hr_docs``, ``finance_docs``).  This tool
embeds the user query with Gemini, runs a similarity search, and returns
the top-k document chunks as a single concatenated context string.
"""

from typing import Annotated

from langchain_core.tools import tool
from langchain_qdrant import QdrantVectorStore
from langgraph.prebuilt import InjectedState
from qdrant_client import QdrantClient

from core.config import settings
from core.llm import get_embedding_model
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

        # --- Async Similarity search (top 5 chunks) ----------------------------
        results = await vector_store.asimilarity_search(query=query, k=5)

        if not results:
            return "No relevant documents found."

        # Include source metadata in the context
        context_parts = []
        for doc in results:
            source = doc.metadata.get("source", "Unknown Source")
            link = doc.metadata.get("link", "#")
            context_parts.append(f"[Source: {source} | Link: {link}]\n{doc.page_content}")

        return "\n\n---\n\n".join(context_parts)

    except Exception as e:
        # Collection may not exist yet, or Qdrant may be unreachable
        print(f"DEBUG: Qdrant Search Error: {e}")
        return "No relevant documents found."
