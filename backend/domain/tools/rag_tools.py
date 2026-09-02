"""
Hybrid RAG tool for searching agent-specific knowledge bases.

Default behavior:
- Normal agents use Qdrant only.
- LifeStore uses Qdrant vector search + Neo4j graph search.

Qdrant:
- Best for semantic product descriptions and fuzzy product matching.

Neo4j:
- Best for structured product facts such as stock_status, price, brand,
  category, product_type, URL, and graph relationships.
"""

import json
import logging
import os
from pathlib import Path
from typing import Annotated, Any, Optional, Dict, List

import httpx
from langchain_core.tools import tool
from langchain_qdrant import QdrantVectorStore, FastEmbedSparse, RetrievalMode
from langgraph.prebuilt import InjectedState
from qdrant_client import QdrantClient

from core.config import evidence_storage_dir, settings, agent_collection_name, collection_suffix
from core.llm import get_embedding_model

from core.reranker import rerank_documents

log = logging.getLogger(__name__)

_sparse_embeddings = FastEmbedSparse(model_name="Qdrant/bm25")

try:
    from neo4j import GraphDatabase
except Exception:
    GraphDatabase = None

# ── Per-thread evidence cache ───────────────────────────────────────────
# search_knowledge_base collects image/table evidence from retrieved Qdrant
# chunks (attached at ingestion time as doc.metadata["evidence"]). chat.py
# consumes this cache after the final answer is generated and streams the
# best matching evidence to the frontend.
_THREAD_EVIDENCE_CACHE: dict[str, list[dict[str, Any]]] = {}


def clear_thread_evidence(thread_id: str | None) -> None:
    """Clear evidence collected for a thread before a new chat request starts."""
    if thread_id:
        _THREAD_EVIDENCE_CACHE.pop(thread_id, None)


def _evidence_key(item: dict[str, Any]) -> tuple:
    """Stable key used for deduplicating evidence items."""
    return (
        item.get("type"),
        item.get("source"),
        item.get("page"),
        item.get("url"),
        (item.get("content") or "")[:200],
    )


def _dedupe_evidence(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove duplicate evidence items while preserving order."""
    seen = set()
    unique: list[dict[str, Any]] = []

    for item in items:
        key = _evidence_key(item)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)

    return unique


def consume_thread_evidence(thread_id: str | None, max_items: int | None = None) -> list[dict[str, Any]]:
    """Return and clear collected evidence for this thread."""
    if not thread_id:
        return []

    items = _THREAD_EVIDENCE_CACHE.pop(thread_id, [])
    items = _dedupe_evidence(items)

    if max_items is not None:
        return items[:max_items]

    return items


def _add_thread_evidence(
    thread_id: str | None,
    evidence_items: Any,
    source: str,
    link: str,
) -> None:
    """Add valid evidence metadata from retrieved Qdrant chunks."""
    if not thread_id or not evidence_items:
        return

    if not isinstance(evidence_items, list):
        return

    cleaned_items: list[dict[str, Any]] = []

    for raw_item in evidence_items:
        if not isinstance(raw_item, dict):
            continue

        item = dict(raw_item)
        evidence_type = item.get("type")

        if evidence_type not in ("image", "table"):
            continue

        # Image evidence must have a URL. Table evidence must have content.
        if evidence_type == "image" and not item.get("url"):
            continue

        if evidence_type == "table" and not item.get("content"):
            continue

        item.setdefault("source", source)
        item.setdefault("link", link)
        cleaned_items.append(item)

    if cleaned_items:
        _THREAD_EVIDENCE_CACHE.setdefault(thread_id, []).extend(cleaned_items)


def _absolutize_remote_evidence(evidence_items: Any, base_url: str) -> list[dict[str, Any]]:
    """Rewrite relative evidence image URLs to absolute URLs on the remote host.

    Evidence PNG crops live on the remote (prod VM) under EVIDENCE_URL_PREFIX
    and are stored as relative URLs. A local dev reading prod vectors must
    point <img> at the remote host, so we prefix relative urls with base_url.
    """
    if not isinstance(evidence_items, list):
        return []

    base = (base_url or "").rstrip("/")
    rewritten: list[dict[str, Any]] = []

    for raw_item in evidence_items:
        if not isinstance(raw_item, dict):
            continue

        item = dict(raw_item)
        url = item.get("url")

        if url and isinstance(url, str) and not url.startswith(("http://", "https://")):
            item["url"] = f"{base}{url}" if url.startswith("/") else f"{base}/{url}"

        rewritten.append(item)

    return rewritten


async def _search_remote(
    agent_id: str,
    query: str,
    k: int = 5,
    thread_id: str | None = None,
) -> str:
    
    """Proxy retrieval to a remote Ask SLT instance's /api/v1/kb endpoint.

    Used by local dev environments to skip ingestion and read prod vectors.
    Also collects any visual/table evidence returned by the remote so devs
    pointing at prod can see diagrams (with image URLs rewritten to the
    remote host where the crops are actually served).
    """
    base = settings.KB_REMOTE_URL.rstrip("/")
    url = f"{base}/api/v1/kb/{agent_id}/retrieve"
    headers = {"X-API-Key": settings.KB_REMOTE_API_KEY or ""}

    #Step 1: Over-fetch 30 candidate chunks from remote endpoint
    OVERFETCH_K = 12
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, json={"query": query, "top_k": OVERFETCH_K}, headers=headers)
        if resp.status_code == 404:
            return "[KB_UNAVAILABLE] No knowledge base is configured for this agent."
        resp.raise_for_status()
        candidate_chunks = resp.json().get("chunks", [])

    if not candidate_chunks:
        return "No relevant documents found."
    
    #Step 2. Extract text and run cross-encodeer reranking
    candidate_texts = [c.get("text", "") for c in candidate_chunks]

    try:
        scores = rerank_documents(query=query, documents=candidate_texts)
    except Exception as rerank_err:
        log.warning(
            "Remote reranking failed for agent='%s'; falling back to raw remote order: %s: %s",
            agent_id,
            type(rerank_err).__name__,
            rerank_err,
        )

        # Fallback: maintain raw remote order if cross-encoder fails
        scores = [1.0 - (i * 0.01) for i in range(len(candidate_chunks))]

    # Pair and sort chunks descending by score
    scored_chunks = list(zip(candidate_chunks, scores))
    scored_chunks.sort(key=lambda item: item[1], reverse=True)

    # Step 3: Filter by relevance score threshold & truncate to requested top-k
    SCORE_THRESHOLD = 0.25
    relevant_scored_chunks = [
        (c, score) for c, score in scored_chunks if score >= SCORE_THRESHOLD
    ]
    top_scored_chunks = relevant_scored_chunks[:k]

    if not top_scored_chunks:
        top_raw_score = scored_chunks[0][1] if scored_chunks else 0.0
        log.info(
            "No remote candidate documents met score threshold (>= %.2f) for agent='%s'. Best raw score: %.4f",
            SCORE_THRESHOLD,
            agent_id,
            top_raw_score,
        )
        return "No relevant remote documents found in the knowledge base matching this query."
    
    # Step 4: Collect evidence and build context ONLY for top reranked chunks
    context_parts = []
    for idx, (c, score) in enumerate(top_scored_chunks):
        source = c.get("source", "Unknown Source")
        link = c.get("link", "#")
        title = c.get("title") or source or "Untitled"

        evidence = c.get("evidence")
        if evidence:
            _add_thread_evidence(
                thread_id=thread_id,
                evidence_items=_absolutize_remote_evidence(evidence, base),
                source=source,
                link=link,
            )
        context_parts.append(
            f"--- Document Chunk #{idx + 1} ---\n"
            f"[Remote Source: {source} | Link: {link} | Title: {title} | Relevancy Score: {score:.4f}]\n"
            f"{c.get('text', '')}"
        )

    log.info(
        "Remote KB retrieval + rerank success agent='%s' candidate_count=%d final_count=%d top_score=%.4f",
        agent_id,
        len(candidate_chunks),
        len(context_parts),
        top_scored_chunks[0][1],
    )

    return "\n\n---\n\n".join(context_parts)        

_neo4j_driver = None


def _get_setting(name: str, default: Optional[str] = None) -> Optional[str]:
    return getattr(settings, name, default)


def _is_lifestore_agent(agent_id: str) -> bool:
    return "lifestore" in agent_id.lower()


def _resolve_collection_name(agent_id: str) -> str:
    """
    Resolve the actual Qdrant collection used for retrieval.

    Important:
    - Ingestion may receive collection_name="lifestore".
    - The backend ingestion layer creates the real Qdrant collection as "lifestore_docs".
    - Retrieval must search the real Qdrant collection, not the base name.
    """
    if _is_lifestore_agent(agent_id):
        # Explicit overrides are used verbatim — the operator names the exact
        # collection (including any provider suffix) themselves.
        explicit_search_collection = _get_setting(
            "LIFESTORE_QDRANT_SEARCH_COLLECTION",
            None,
        )

        if explicit_search_collection:
            return explicit_search_collection

        delete_collection = _get_setting(
            "LIFESTORE_QDRANT_DELETE_COLLECTION",
            None,
        )

        if delete_collection:
            return delete_collection

        base_collection = _get_setting("LIFESTORE_QDRANT_COLLECTION", "lifestore")

        if not base_collection.endswith("_docs"):
            base_collection = f"{base_collection}_docs"

        # Namespace by embedding provider so gemini/openai collections coexist.
        return f"{base_collection}{collection_suffix()}"

    return agent_collection_name(agent_id)


def _get_neo4j_driver():
    global _neo4j_driver

    if _neo4j_driver is not None:
        return _neo4j_driver

    if GraphDatabase is None:
        raise RuntimeError("neo4j package is not installed.")

    neo4j_uri = _get_setting("NEO4J_URI")
    neo4j_username = _get_setting("NEO4J_USERNAME")
    neo4j_password = _get_setting("NEO4J_PASSWORD")

    if not neo4j_uri:
        raise RuntimeError("NEO4J_URI is not configured.")

    if not neo4j_username or not neo4j_password:
        raise RuntimeError("NEO4J_USERNAME or NEO4J_PASSWORD is not configured.")

    _neo4j_driver = GraphDatabase.driver(
        neo4j_uri,
        auth=(neo4j_username, neo4j_password),
    )

    return _neo4j_driver

"""
async def get_agent_context(
    query: str,
    agent_id: str,
    k: int = 6,
    thread_id: str | None = None,
) -> str:

    # 1. Fetch reranked vector context (automatically routes local vs. remote)
    vector_context = await _search_qdrant_knowledge_base(
        query=query, agent_id=agent_id, k=k, thread_id=thread_id
    )

    # 2. Fetch graph context if Neo4j is configured
    graph_context = await _search_neo4j_graph(query=query, agent_id=agent_id)

    # 3. Assemble combined payload
    context_sections = []

    if graph_context:
        context_sections.append(
            f"### STRUCTURED KNOWLEDGE GRAPH FACTS:\n{graph_context}"
        )

    if vector_context:
        context_sections.append(
            f"### UNSTRUCTURED VECTOR DOCUMENT CHUNKS:\n{vector_context}"
        )

    if not context_sections:
        return "No relevant knowledge base or graph context found for this query."

    return "\n\n========================================\n\n".join(context_sections)
"""
   


async def _search_qdrant_knowledge_base(
    query: str,
    agent_id: str,
    k: int = 5, # final top-k chunks to keep after reranking
    thread_id: str | None = None,
) -> str:
    """Search the Qdrant knowledge base for documents relevant to the user's query.

    Uses hybrid retrieval (dense semantic + BM25 lexical) to balance
    semantic understanding with exact-match recall on codes, IDs, and
    proper nouns.

    + cross-encoder reranking

    Args:
        query: The user's natural-language question.
        agent_id: Identifier for the target agent (e.g. "hr", "finance").
                  Determines the Qdrant collection searched.

    Returns:
        A concatenated string of the most relevant document chunks,
        or an informational message when no documents are found.
    """
    collection_name = _resolve_collection_name(agent_id)

    if settings.KB_REMOTE_URL:
        try:
            remote_res = await _search_remote(agent_id, query, k=5, thread_id=thread_id)
            if isinstance(remote_res, dict):
                return remote_res
            return {"context": str(remote_res), "image_paths": []}
        except Exception as e:
            log.exception(
                f"Remote KB retrieval failed for agent='{agent_id}' "
                f"url='{settings.KB_REMOTE_URL}': {type(e).__name__}: {e}"
            )
            return {"context": "No relevant documents found.", "image_paths": []}


    try:
        embeddings = get_embedding_model()
        client = QdrantClient(url=settings.QDRANT_URL)

        try:
            collection_present = client.collection_exists(collection_name)
        except Exception as probe_err:
            log.warning(
                "Qdrant collection_exists probe failed for '%s': %s: %s",
                collection_name,
                type(probe_err).__name__,
                probe_err,
            )
            collection_present = True

        if not collection_present:
            log.error(
                "Qdrant collection '%s' does not exist for agent '%s'.",
                collection_name,
                agent_id,
            )
            return {
                "context": "[QDRANT_UNAVAILABLE] No Qdrant knowledge base is configured for this agent.",
                "image_paths": [],
            }

        vector_store = QdrantVectorStore(
            client=client,
            collection_name=collection_name,
            embedding=embeddings,
            sparse_embedding=_sparse_embeddings,
            retrieval_mode=RetrievalMode.HYBRID,
            vector_name="dense",
            sparse_vector_name="sparse",
        )

        #Step -1 Over -fetch candidates from Qdrant (Fetch top 30)
        OVERFETCH_K = 12
        candidate_docs = await vector_store.asimilarity_search(query=query, k=OVERFETCH_K)

        if not candidate_docs:
            log.info(
                "Qdrant search returned 0 results for agent='%s' collection='%s' query='%s'",
                agent_id,
                collection_name,
                query,
            )
            return {"context": "No relevant vector documents found.", "image_paths": []}
        
        #Step -2 Perform cross-encoder reranking
        candidate_texts = [doc.page_content for doc in candidate_docs]
        
        try:
            scores = rerank_documents(query=query, documents=candidate_texts)
        except Exception as rerank_err:
            log.warning(
                "Reranking failed for agent='%s'; falling back to raw Qdrant order: %s: %s",
                agent_id,
                type(rerank_err).__name__,
                rerank_err,
            )
            # Fallback: maintain Qdrant's original similarity ranking if model fails
            scores = [1.0 - (i * 0.01) for i in range(len(candidate_docs))]

        # Pair each document with its  reranking corresponding score
        scored_docs = list(zip(candidate_docs, scores))

        # Sort documents by score in descending order
        scored_docs.sort(key=lambda item: item[1], reverse=True)


        #Step -3 Apply relevence score threshold & Truncate
        # BGE-Reranker v2 M3 normalized scores: 
        # >= 0.50: Very high relevance
        # 0.25 - 0.49: Moderate relevance
        # < 0.25: Low relevance / Noise
        SCORE_THRESHOLD = 0.25

        #Keep only docs that satisfy the score threshold
        relevant_scored_docs = [
            (doc, score) for doc, score in scored_docs if score >= SCORE_THRESHOLD]

        # Truncate to final top-k requested chunks
        top_scored_docs = relevant_scored_docs[:k]

        # FALLBACK: If nothing passed the threshold, log and notify
        if not top_scored_docs:
            top_raw_score = scored_docs[0][1] if scored_docs else 0.0
            log.info(
                "No candidate documents met score threshold (>= %.2f) for agent='%s'. Best raw score: %.4f",
                SCORE_THRESHOLD,
                agent_id,
                top_raw_score,
            )
            return {
                "context": "No relevant vector documents found in the knowledge base matching this query.",
                "image_paths": [],
            }
        
        #Step -4 Collect evidence and format final context for return
        context_parts = []
        extracted_image_paths = []

        for idx, (doc, score) in enumerate(top_scored_docs):
            source = (
                doc.metadata.get("source")
                or doc.metadata.get("source_url")
                or "Unknown Source"
            )

            link = (
                doc.metadata.get("link")
                or doc.metadata.get("source_url")
                or "#"
            )

            title = doc.metadata.get("title") or source or "Untitled"

            # Extract visual image path from metadata if present
            img_path = (
                doc.metadata.get("image_path")
                or doc.metadata.get("visual_path")
                or doc.metadata.get("evidence_path")
            )
            asset_path = evidence_storage_dir() / os.path.basename(str(img_path or ""))
            if img_path and not asset_path.is_file():
                log.warning(
                    "Skipping missing visual evidence asset for agent='%s': %s",
                    agent_id,
                    asset_path,
                )
            elif img_path and img_path not in extracted_image_paths:
                extracted_image_paths.append(img_path)
                
                # Convert image path to evidence item for frontend rendering
                visual_evidence_item = {
                    "type": "image",
                    "url": f"{settings.EVIDENCE_URL_PREFIX}/{asset_path.name}",
                    "source": source,
                    "link": link,
                    "title": f"Visual Evidence - {title}",
                    "page": doc.metadata.get("page_number", 1),
                }
                _add_thread_evidence(
                    thread_id=thread_id,
                    evidence_items=[visual_evidence_item],
                    source=source,
                    link=link,
                )

            #Collect visual/table evidence ONLY for top reranked chunks
            _add_thread_evidence(
                thread_id=thread_id,
                evidence_items=doc.metadata.get("evidence") or [],
                source=source,
                link=link,
            )

            context_parts.append(
                f"--- Document Chunk #{idx + 1} ---\n"
                f"[Vector Source: {source} | Link: {link} | Title: {title} | Relevancy Score: {score:.4f}]\n"
                f"{doc.page_content}"
            )

        log.info(
            "Qdrant search + rerank success agent='%s' collection='%s' candidate_count=%d final_count=%d top_score=%.4f",
            agent_id,
            collection_name,
            len(candidate_docs),
            len(context_parts),
            top_scored_docs[0][1],
        )

        return {
            "context": "\n\n---\n\n".join(context_parts),
            "image_paths": extracted_image_paths,
        }
    
    except Exception as exc:
        log.exception(
            "Qdrant hybrid search failed for agent='%s' collection='%s': %s: %s",
            agent_id,
            collection_name,
            type(exc).__name__,
            exc,
        )

        return {"context": "No relevant vector documents found.", "image_paths": []}
    


def _clean_search_terms(query: str) -> list[str]:
    cleaned = query.lower()

    remove_words = [
        "is", "in", "the", "stock", "available", "availability",
        "do", "you", "have", "give", "me", "list", "all",
        "products", "product", "with", "price", "brand",
        "category", "url", "life", "lifestore", "of", "a", "an",
    ]

    for word in remove_words:
        cleaned = cleaned.replace(f" {word} ", " ")

    cleaned = cleaned.replace("?", " ").replace(",", " ")
    terms = [t.strip() for t in cleaned.split() if len(t.strip()) >= 2]

    # Keep meaningful terms. Example:
    # "is D-Link AC1200 Wi-Fi Range Extender in the stock?"
    # -> ["d-link", "ac1200", "wi-fi", "range", "extender"]
    return list(dict.fromkeys(terms))


def _format_graph_rows(rows: list[dict]) -> str:
    if not rows:
        return "No relevant graph facts found."

    parts = []

    for row in rows:
        specs_json = row.get("specs_json") or "{}"
        tags = row.get("tags") or []

        try:
            specs = json.loads(specs_json) if isinstance(specs_json, str) else specs_json
        except Exception:
            specs = {}

        specs_text = ", ".join(f"{k}: {v}" for k, v in specs.items()) if specs else "None"
        tags_text = ", ".join(str(tag) for tag in tags if tag) if tags else "None"

        description = row.get("description") or ""
        description = " ".join(str(description).split())

        parts.append(
            "\n".join(
                [
                    f"Product: {row.get('product') or 'Unknown'}",
                    f"Brand: {row.get('brand') or 'Unknown'}",
                    f"Seller: {row.get('seller') or 'Unknown'}",
                    f"Category: {row.get('category') or 'Unknown'}",
                    f"Product Type: {row.get('product_type') or 'Unknown'}",
                    f"Price: {row.get('price') or 'Unknown'}",
                    f"Stock Status: {row.get('stock_status') or 'Unknown'}",
                    f"URL: {row.get('url') or 'Unknown'}",
                    f"Image URL: {row.get('image_url') or 'Unknown'}",
                    f"Tags: {tags_text}",
                    f"Specs: {specs_text}",
                    f"Description: {description or 'No description available'}",
                ]
            )
        )

    return "\n\n---\n\n".join(parts)


def _search_lifestore_graph(query: str, limit: int = 10) -> str:
    """
    Structured graph search for LifeStore.

    Neo4j is the source of truth for structured facts:
    - seller
    - stock_status
    - price
    - brand
    - category
    - product_type
    - URL

    Qdrant is still useful for semantic/descriptive content, but this graph
    search should answer exact product facts such as:
    - Who is the seller of TeDi Aroma Diffuser?
    - Is D-Link AC1200 Wi-Fi Range Extender in stock?
    - List out of stock products.
    """
    neo4j_uri = _get_setting("NEO4J_URI")
    neo4j_username = _get_setting("NEO4J_USERNAME")
    neo4j_password = _get_setting("NEO4J_PASSWORD")
    neo4j_database = _get_setting("NEO4J_DATABASE", "neo4j")

    if not neo4j_uri or not neo4j_username or not neo4j_password:
        log.info("Neo4j not configured. Skipping graph search.")
        return "No relevant graph facts found."

    terms = _clean_search_terms(query)
    query_lower = query.lower()

    wants_out_of_stock = (
        "out of stock" in query_lower
        or "out-of-stock" in query_lower
        or "sold out" in query_lower
    )

    wants_stock_info = (
        "in stock" in query_lower
        or "available" in query_lower
        or "availability" in query_lower
        or "stock" in query_lower
        or wants_out_of_stock
    )

    wants_seller_info = (
        "seller" in query_lower
        or "sold by" in query_lower
        or "who sells" in query_lower
        or "who is selling" in query_lower
    )

    try:
        driver = _get_neo4j_driver()

        # ------------------------------------------------------------
        # Case 1: User asks for all out-of-stock products
        # Example: "List all out of stock products"
        # ------------------------------------------------------------
        if wants_out_of_stock and len(terms) == 0:
            cypher = """
            MATCH (p:Product)-[:HAS_AVAILABILITY]->(a:Availability)
            WHERE a.status = "out_of_stock"
            OPTIONAL MATCH (p)-[:MADE_BY]->(b:Brand)
            OPTIONAL MATCH (p)-[:BELONGS_TO]->(c:Category)
            RETURN
                p.name AS product,
                p.seller AS seller,
                b.name AS brand,
                c.name AS category,
                p.product_type AS product_type,
                p.price AS price,
                p.stock_status AS stock_status,
                p.url AS url,
                p.tags AS tags,
                p.specs_json AS specs_json,
                100 AS score
            ORDER BY p.name
            LIMIT $limit
            """

            records, _, _ = driver.execute_query(
                cypher,
                limit=limit,
                database_=neo4j_database,
            )

        # ------------------------------------------------------------
        # Case 2: User asks for stock/seller/specific product facts
        # Uses weighted scoring so exact product-name matches rank first.
        # Example:
        # "Who is the seller of TeDi Aroma Diffuser?"
        # "Is D-Link AC1200 Wi-Fi Range Extender in stock?"
        # ------------------------------------------------------------
        else:
            cypher = """
            MATCH (p:Product)
            OPTIONAL MATCH (p)-[:MADE_BY]->(b:Brand)
            OPTIONAL MATCH (p)-[:BELONGS_TO]->(c:Category)
            OPTIONAL MATCH (p)-[:HAS_AVAILABILITY]->(a:Availability)

            WITH p, b, c, a,
                 toLower(coalesce(p.name, "")) AS name_l,
                 toLower(coalesce(p.description, "")) AS desc_l,
                 toLower(coalesce(p.product_type, "")) AS type_l,
                 toLower(coalesce(p.seller, "")) AS seller_l,
                 toLower(coalesce(p.stock_status, "")) AS stock_l,
                 toLower(coalesce(b.name, "")) AS brand_l,
                 toLower(coalesce(c.name, "")) AS category_l,
                 [tag IN coalesce(p.tags, []) | toLower(tag)] AS tags_l

            WITH p, b, c, a,
                 reduce(score = 0, term IN $terms |
                    score
                    + CASE WHEN name_l = term THEN 20 ELSE 0 END
                    + CASE WHEN name_l CONTAINS term THEN 8 ELSE 0 END
                    + CASE WHEN brand_l CONTAINS term THEN 3 ELSE 0 END
                    + CASE WHEN category_l CONTAINS term THEN 2 ELSE 0 END
                    + CASE WHEN type_l CONTAINS term THEN 2 ELSE 0 END
                    + CASE WHEN seller_l CONTAINS term THEN 2 ELSE 0 END
                    + CASE WHEN desc_l CONTAINS term THEN 1 ELSE 0 END
                    + CASE WHEN any(tag IN tags_l WHERE tag CONTAINS term) THEN 1 ELSE 0 END
                 ) AS score

            WHERE
                (
                    size($terms) = 0
                    OR score > 0
                )
                AND
                (
                    $filter_out_of_stock = false
                    OR p.stock_status = "out_of_stock"
                    OR a.status = "out_of_stock"
                )

            RETURN
                p.name AS product,
                p.seller AS seller,
                b.name AS brand,
                c.name AS category,
                p.product_type AS product_type,
                p.price AS price,
                p.stock_status AS stock_status,
                p.url AS url,
                p.tags AS tags,
                p.specs_json AS specs_json,
                score AS score

            ORDER BY score DESC, p.name
            LIMIT $limit
            """

            records, _, _ = driver.execute_query(
                cypher,
                terms=terms,
                filter_out_of_stock=wants_out_of_stock,
                limit=limit,
                database_=neo4j_database,
            )

        rows = [record.data() for record in records]

        log.info(
            "Neo4j graph search success query='%s' terms=%s wants_stock=%s wants_seller=%s wants_out_of_stock=%s results=%s",
            query,
            terms,
            wants_stock_info,
            wants_seller_info,
            wants_out_of_stock,
            len(rows),
        )

        return _format_graph_rows(rows)

    except Exception as exc:
        log.exception("Neo4j LifeStore graph search failed: %s", exc)
        return "No relevant graph facts found."


@tool
async def search_knowledge_base(
    query: str,
    agent_id: Annotated[str, InjectedState("agent_id")],
    thread_id: Annotated[str | None, InjectedState("thread_id")] = None,
) -> Dict[str, Any]:
    """
    Search knowledge base.

    Normal agents:
    - Qdrant only.

    LifeStore:
    - Qdrant vector retrieval + Neo4j structured graph retrieval.
    """
    qdrant_context = await _search_qdrant_knowledge_base(
        query=query,
        agent_id=agent_id,
        k=12,
        thread_id=thread_id,
    )

    # Standardize result structure if helper returns string vs dict
    if isinstance(qdrant_context, str):
        context = qdrant_context
        image_paths = []
    else:
        
        context = qdrant_context.get("context", "")
        image_paths = qdrant_context.get("image_paths", [])

    if not _is_lifestore_agent(agent_id):
        return {
            "context":context,
            "image_paths": image_paths
        }

    log.info(
        "Hybrid retrieval triggered for LifeStore agent='%s' query='%s'",
        agent_id,
        query,
    )

    graph_context = _search_lifestore_graph(query=query, limit=12)

    combined_context = f"""
[NEO4J GRAPH FACTS - VERIFIED PRODUCT FACTS. USE THESE FOR NAME, BRAND, SELLER, PRICE, STOCK, CATEGORY, PRODUCT TYPE, URL, DESCRIPTION, FEATURES, AND SPECIFICATIONS]
{graph_context}

[QDRANT VECTOR CONTEXT - VERIFIED PRODUCT PAGE CONTENT. USE THIS FOR DESCRIPTIONS, FUNCTIONALITIES, FEATURES, SPECIFICATIONS, AND GENERAL PRODUCT DETAILS]
{qdrant_context}
""".strip()

    return {
        "context": combined_context,
        "image_paths": image_paths
    }