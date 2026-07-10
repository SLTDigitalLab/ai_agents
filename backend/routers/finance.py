"""
Finance knowledge-base retrieval endpoint for external clients (e.g. voice assistant).

Exposes hybrid (dense + BM25) search over the ``finance_docs`` Qdrant collection
and returns raw chunks so the caller can run its own LLM (e.g. local Ollama) to
generate a spoken answer.
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from langchain_qdrant import QdrantVectorStore, RetrievalMode
from pydantic import BaseModel, Field
from qdrant_client import QdrantClient

from core.config import settings
from core.llm import get_embedding_model
from domain.tools.rag_tools import _sparse_embeddings

router = APIRouter(prefix="/api/v1/finance", tags=["Finance KB"])
logger = logging.getLogger(__name__)

COLLECTION_NAME = "finance_docs"


class RetrieveRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(5, ge=1, le=25)


class Chunk(BaseModel):
    text: str
    source: str
    link: str
    score: Optional[float] = None


class RetrieveResponse(BaseModel):
    query: str
    chunks: List[Chunk]


def require_api_key(x_api_key: str = Header(..., alias="X-API-Key")) -> None:
    expected = settings.VOICE_ASSISTANT_API_KEY
    if not expected:
        raise HTTPException(status_code=503, detail="Endpoint not configured")
    if x_api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid API key")


@router.post("/retrieve", response_model=RetrieveResponse, dependencies=[Depends(require_api_key)])
async def retrieve(req: RetrieveRequest) -> RetrieveResponse:
    client = QdrantClient(url=settings.QDRANT_URL)

    try:
        if not client.collection_exists(COLLECTION_NAME):
            raise HTTPException(status_code=404, detail=f"Collection '{COLLECTION_NAME}' not found")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Qdrant probe failed: {type(e).__name__}: {e}")
        raise HTTPException(status_code=502, detail="Vector store unavailable")

    vector_store = QdrantVectorStore(
        client=client,
        collection_name=COLLECTION_NAME,
        embedding=get_embedding_model(),
        sparse_embedding=_sparse_embeddings,
        retrieval_mode=RetrievalMode.HYBRID,
        vector_name="dense",
        sparse_vector_name="sparse",
    )

    try:
        results = await vector_store.asimilarity_search_with_score(query=req.query, k=req.top_k)
    except Exception as e:
        logger.exception(f"Finance hybrid search failed: {type(e).__name__}: {e}")
        raise HTTPException(status_code=502, detail="Search failed")

    chunks = [
        Chunk(
            text=doc.page_content,
            source=doc.metadata.get("source", "Unknown Source"),
            link=doc.metadata.get("link", "#"),
            score=float(score) if score is not None else None,
        )
        for doc, score in results
    ]

    return RetrieveResponse(query=req.query, chunks=chunks)
