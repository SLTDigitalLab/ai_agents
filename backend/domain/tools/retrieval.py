import logging
from langchain_core.documents import Document
from langchain_qdrant import QdrantVectorStore, RetrievalMode, FastEmbedSparse
from qdrant_client import QdrantClient

from core.config import settings, agent_collection_name
from core.llm import get_embedding_model
from core.reranker import rerank_documents  # Import your existing singleton reranker

log = logging.getLogger(__name__)


def retrieve_and_rerank_docs(
    query: str, 
    agent_name: str, 
    top_k: int = 10, 
    rerank_k: int = 4
) -> dict:
    """
    1. Executes Qdrant Hybrid Search (Dense + BM25).
    2. Reranks candidate text and visual_description chunks using core/reranker.py.
    3. Separates plain text context from visual payload metadata.
    """
    client = QdrantClient(url=settings.QDRANT_URL)
    embeddings = get_embedding_model()
    sparse_embeddings = FastEmbedSparse(model_name="Qdrant/bm25")
    collection_name = agent_collection_name(agent_name)

    # 1. Initialize Hybrid Qdrant Vector Store
    vector_store = QdrantVectorStore(
        client=client,
        collection_name=collection_name,
        embedding=embeddings,
        sparse_embedding=sparse_embeddings,
        retrieval_mode=RetrievalMode.HYBRID,
        vector_name="dense",
        sparse_vector_name="sparse",
    )

    # 2. Hybrid Retrieval (Initial Candidate Pool)
    candidate_docs: list[Document] = vector_store.similarity_search(query, k=top_k)
    if not candidate_docs:
        log.warning(f"No candidate documents returned for query: '{query}'")
        return {"text_context": "", "visual_evidence": []}

    # 3. Use your EXISTING rerank_documents function
    raw_texts = [doc.page_content for doc in candidate_docs]
    scores = rerank_documents(query=query, documents=raw_texts)

    # Pair candidates with their reranker scores & sort descending
    scored_docs = sorted(
        zip(candidate_docs, scores), 
        key=lambda item: item[1], 
        reverse=True
    )
    top_reranked = [doc for doc, score in scored_docs[:rerank_k]]

    # 4. Separate Text Chunks vs Visual Evidence Payloads
    text_contexts = []
    visual_evidence = []

    for doc in top_reranked:
        chunk_type = doc.metadata.get("type", "text")

        if chunk_type == "visual_description":
            # Extract image evidence metadata
            visual_evidence.append({
                "doc_id": doc.metadata.get("doc_id", "unknown"),
                "source": doc.metadata.get("source", "document"),
                "page_number": doc.metadata.get("page_number", 1),
                "image_path": doc.metadata.get("image_path", ""),
                "visual_description": doc.page_content,
            })
            # Include visual description string so the LLM has contextual awareness
            page_num = doc.metadata.get("page_number", 1)
            text_contexts.append(f"[Visual Evidence - Page {page_num}]: {doc.page_content}")
        else:
            text_contexts.append(doc.page_content)

    return {
        "text_context": "\n\n".join(text_contexts),
        "visual_evidence": visual_evidence,
    }