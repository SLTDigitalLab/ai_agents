"""
Generic knowledge-base retrieval endpoint for any ingested agent.

Exposes hybrid (dense + BM25) search over ``{agent_id}_docs`` collections so
that local developer instances can query prod-embedded vectors instead of
re-embedding source files locally. Gated by an allowlist + API key.
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Path
from langchain_qdrant import QdrantVectorStore, RetrievalMode
from pydantic import BaseModel, Field
from qdrant_client import QdrantClient

from core.config import settings
from core.llm import get_embedding_model
from core.llm_slm import get_slm_embedding_model
from domain.tools.rag_tools import _sparse_embeddings

router = APIRouter(prefix="/api/v1/kb", tags=["KB Retrieval"])
logger = logging.getLogger(__name__)


class RetrieveRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(10, ge=1, le=25)


class Chunk(BaseModel):
    text: str
    source: str
    link: str
    score: Optional[float] = None


class RetrieveResponse(BaseModel):
    agent_id: str
    query: str
    chunks: List[Chunk]


def _allowlist() -> set[str]:
    return {a.strip() for a in settings.KB_RETRIEVAL_ALLOWLIST.split(",") if a.strip()}


def require_api_key(x_api_key: str = Header(..., alias="X-API-Key")) -> None:
    expected = settings.DEV_KB_API_KEY
    if not expected:
        raise HTTPException(status_code=503, detail="Endpoint not configured")
    if x_api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid API key")


@router.post(
    "/{agent_id}/retrieve",
    response_model=RetrieveResponse,
    dependencies=[Depends(require_api_key)],
)
async def retrieve(
    req: RetrieveRequest,
    agent_id: str = Path(..., min_length=1, max_length=64, pattern=r"^[a-z0-9_-]+$"),
) -> RetrieveResponse:
    if agent_id not in _allowlist():
        raise HTTPException(status_code=404, detail="Unknown agent")

    collection_name = f"{agent_id}_docs"
    client = QdrantClient(url=settings.QDRANT_URL)

    try:
        if not client.collection_exists(collection_name):
            raise HTTPException(status_code=404, detail=f"Collection '{collection_name}' not found")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Qdrant probe failed: {type(e).__name__}: {e}")
        raise HTTPException(status_code=502, detail="Vector store unavailable")

    embedding = get_slm_embedding_model() if agent_id == "askhrslm" else get_embedding_model()

    vector_store = QdrantVectorStore(
        client=client,
        collection_name=collection_name,
        embedding=embedding,
        sparse_embedding=_sparse_embeddings,
        retrieval_mode=RetrievalMode.HYBRID,
        vector_name="dense",
        sparse_vector_name="sparse",
    )

    try:
        results = await vector_store.asimilarity_search_with_score(query=req.query, k=req.top_k)
    except Exception as e:
        logger.exception(f"KB hybrid search failed for '{agent_id}': {type(e).__name__}: {e}")
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

    return RetrieveResponse(agent_id=agent_id, query=req.query, chunks=chunks)
