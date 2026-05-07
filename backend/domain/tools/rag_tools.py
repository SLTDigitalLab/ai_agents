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
from typing import Annotated, Optional

from langchain_core.tools import tool
from langchain_qdrant import QdrantVectorStore, FastEmbedSparse, RetrievalMode
from langgraph.prebuilt import InjectedState
from qdrant_client import QdrantClient

from core.config import settings
from core.llm import get_embedding_model

log = logging.getLogger(__name__)

_sparse_embeddings = FastEmbedSparse(model_name="Qdrant/bm25")

try:
    from neo4j import GraphDatabase
except Exception:
    GraphDatabase = None


_neo4j_driver = None


def _get_setting(name: str, default: Optional[str] = None) -> Optional[str]:
    return getattr(settings, name, default)


def _is_lifestore_agent(agent_id: str) -> bool:
    return "lifestore" in agent_id.lower()


def _resolve_collection_name(agent_id: str) -> str:
    lifestore_collection = _get_setting("LIFESTORE_QDRANT_COLLECTION", None)

    if _is_lifestore_agent(agent_id) and lifestore_collection:
        return lifestore_collection

    return f"{agent_id}_docs"


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


async def _search_qdrant_knowledge_base(
    query: str,
    agent_id: str,
    k: int = 12,
) -> str:
    collection_name = _resolve_collection_name(agent_id)

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
            return "[QDRANT_UNAVAILABLE] No Qdrant knowledge base is configured for this agent."

        vector_store = QdrantVectorStore(
            client=client,
            collection_name=collection_name,
            embedding=embeddings,
            sparse_embedding=_sparse_embeddings,
            retrieval_mode=RetrievalMode.HYBRID,
            vector_name="dense",
            sparse_vector_name="sparse",
        )


        results = await vector_store.asimilarity_search(query=query, k=k)

        if not results:
            log.info(
                "Qdrant search returned 0 results for agent='%s' collection='%s' query='%s'",
                agent_id,
                collection_name,
                query,
            )
            return "No relevant vector documents found."

        context_parts = []

        for doc in results:
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

            title = doc.metadata.get("title") or "Untitled"

            context_parts.append(
                f"[Vector Source: {source} | Link: {link} | Title: {title}]\n"
                f"{doc.page_content}"
            )

        log.info(
            "Qdrant search success agent='%s' collection='%s' results=%s",
            agent_id,
            collection_name,
            len(results),
        )

        return "\n\n---\n\n".join(context_parts)

    except Exception as exc:
        log.exception(
            "Qdrant hybrid search failed for agent='%s' collection='%s': %s: %s",
            agent_id,
            collection_name,
            type(exc).__name__,
            exc,
        )
        return "No relevant vector documents found."


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
        tags_text = ", ".join(tags) if tags else "None"

        parts.append(
            "\n".join(
                [
                    f"Product: {row.get('product') or 'Unknown'}",
                    f"Brand: {row.get('brand') or 'Unknown'}",
                    f"Category: {row.get('category') or 'Unknown'}",
                    f"Product Type: {row.get('product_type') or 'Unknown'}",
                    f"Price: {row.get('price') or 'Unknown'}",
                    f"Stock Status: {row.get('stock_status') or 'Unknown'}",
                    f"URL: {row.get('url') or 'Unknown'}",
                    f"Tags: {tags_text}",
                    f"Specs: {specs_text}",
                ]
            )
        )

    return "\n\n---\n\n".join(parts)


def _search_lifestore_graph(query: str, limit: int = 10) -> str:
    """
    Structured graph search for LifeStore.

    This is what fixes stock questions. The graph has product.stock_status
    and Product -> Availability, so the LLM can answer availability directly.
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

    wants_in_stock = (
        "in stock" in query_lower
        or "available" in query_lower
        or "availability" in query_lower
        or "stock" in query_lower
    ) and not wants_out_of_stock

    try:
        driver = _get_neo4j_driver()

        if wants_out_of_stock:
            cypher = """
            MATCH (p:Product)-[:HAS_AVAILABILITY]->(a:Availability)
            WHERE a.status = "out_of_stock"
            OPTIONAL MATCH (p)-[:MADE_BY]->(b:Brand)
            OPTIONAL MATCH (p)-[:BELONGS_TO]->(c:Category)
            RETURN
                p.name AS product,
                b.name AS brand,
                c.name AS category,
                p.product_type AS product_type,
                p.price AS price,
                p.stock_status AS stock_status,
                p.url AS url,
                p.tags AS tags,
                p.specs_json AS specs_json
            ORDER BY p.name
            LIMIT $limit
            """

            records, _, _ = driver.execute_query(
                cypher,
                limit=limit,
                database_=neo4j_database,
            )

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
                 toLower(coalesce(b.name, "")) AS brand_l,
                 toLower(coalesce(c.name, "")) AS category_l,
                 [tag IN coalesce(p.tags, []) | toLower(tag)] AS tags_l
            WHERE
                size($terms) = 0
                OR all(term IN $terms WHERE
                    name_l CONTAINS term
                    OR desc_l CONTAINS term
                    OR type_l CONTAINS term
                    OR brand_l CONTAINS term
                    OR category_l CONTAINS term
                    OR any(tag IN tags_l WHERE tag CONTAINS term)
                )
                OR any(term IN $terms WHERE
                    name_l CONTAINS term
                    OR brand_l CONTAINS term
                    OR category_l CONTAINS term
                    OR type_l CONTAINS term
                )
            RETURN
                p.name AS product,
                b.name AS brand,
                c.name AS category,
                p.product_type AS product_type,
                p.price AS price,
                p.stock_status AS stock_status,
                p.url AS url,
                p.tags AS tags,
                p.specs_json AS specs_json
            ORDER BY p.name
            LIMIT $limit
            """

            records, _, _ = driver.execute_query(
                cypher,
                terms=terms,
                limit=limit,
                database_=neo4j_database,
            )

        rows = [record.data() for record in records]

        log.info(
            "Neo4j graph search success query='%s' terms=%s results=%s",
            query,
            terms,
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
) -> str:
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
    )

    if not _is_lifestore_agent(agent_id):
        return qdrant_context

    log.info(
        "Hybrid retrieval triggered for LifeStore agent='%s' query='%s'",
        agent_id,
        query,
    )

    graph_context = _search_lifestore_graph(query=query, limit=12)

    return f"""
[NEO4J GRAPH FACTS - USE THESE FOR STOCK, PRICE, BRAND, CATEGORY, PRODUCT TYPE, URL]
{graph_context}

[QDRANT VECTOR CONTEXT - USE THIS FOR DESCRIPTIONS AND GENERAL PRODUCT DETAILS]
{qdrant_context}
""".strip()