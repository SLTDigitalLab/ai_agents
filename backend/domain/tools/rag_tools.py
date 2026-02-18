"""
RAG tool for searching agent-specific Qdrant knowledge-base collections.

Each agent stores its documents in a dedicated Qdrant collection named
``<agent_id>_docs`` (e.g. ``hr_docs``, ``finance_docs``).  This tool
embeds the user query with Gemini, runs a similarity search, and returns
the top-k document chunks as a single concatenated context string.
"""

from typing import Annotated

from langchain_core.tools import tool
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_qdrant import QdrantVectorStore
from langgraph.prebuilt import InjectedState
from qdrant_client import QdrantClient

from core.config import settings


@tool
def search_knowledge_base(
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
        # --- Embeddings (Gemini embedding-001, 3072 dimensions) -----------
        embeddings = GoogleGenerativeAIEmbeddings(
            model="models/gemini-embedding-001",
            google_api_key=settings.GOOGLE_API_KEY,
        )

        # --- Qdrant client & collection ----------------------------------
        client = QdrantClient(url=settings.QDRANT_URL)
        collection_name = f"{agent_id}_docs"

        vector_store = QdrantVectorStore(
            client=client,
            collection_name=collection_name,
            embedding=embeddings,
        )

        # --- Similarity search (top 8 chunks) ----------------------------
        results = vector_store.similarity_search(query=query, k=8)

        if not results:
            return "No relevant documents found."

        # Concatenate page contents separated by double newlines
        context = "\n\n".join(doc.page_content for doc in results)
        
        # --- DEBUG LOGGER ADDED HERE ---
        # This will print the raw text Qdrant found into your Docker terminal
        print("\n" + "="*40)
        print(f"DEBUG: RAW CONTEXT FOR '{query}'")
        print("="*40)
        print(context)
        print("="*40 + "\n")

        return context

    except Exception:
        # Collection may not exist yet, or Qdrant may be unreachable
        return "No relevant documents found."
