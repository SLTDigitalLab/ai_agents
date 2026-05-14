import logging
from functools import lru_cache

from neo4j import GraphDatabase

from core.config import settings


log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_neo4j_driver():
    if not settings.NEO4J_URI:
        raise RuntimeError("NEO4J_URI is not configured.")

    if not settings.NEO4J_USERNAME or not settings.NEO4J_PASSWORD:
        raise RuntimeError("NEO4J_USERNAME or NEO4J_PASSWORD is not configured.")

    driver = GraphDatabase.driver(
        settings.NEO4J_URI,
        auth=(settings.NEO4J_USERNAME, settings.NEO4J_PASSWORD),
    )

    driver.verify_connectivity()
    return driver


def search_lifestore_graph(query: str, limit: int = 10) -> str:
    """
    Search LifeStore product graph in Neo4j.

    This is a keyword-based graph search. It is useful for finding structured
    product facts such as product name, brand, category, tags, and specs.
    """

    cypher = """
    MATCH (p:Product)
    OPTIONAL MATCH (p)-[:MADE_BY]->(b:Brand)
    OPTIONAL MATCH (p)-[:BELONGS_TO]->(c:Category)
    OPTIONAL MATCH (p)-[:HAS_TAG]->(t:Tag)
    OPTIONAL MATCH (p)-[:HAS_SPEC]->(s:Spec)
    WHERE
        toLower(coalesce(p.name, "")) CONTAINS toLower($query)
        OR toLower(coalesce(p.title, "")) CONTAINS toLower($query)
        OR toLower(coalesce(p.description, "")) CONTAINS toLower($query)
        OR toLower(coalesce(b.name, "")) CONTAINS toLower($query)
        OR toLower(coalesce(c.name, "")) CONTAINS toLower($query)
        OR toLower(coalesce(t.name, "")) CONTAINS toLower($query)
        OR toLower(coalesce(s.name, "")) CONTAINS toLower($query)
        OR toLower(coalesce(s.value, "")) CONTAINS toLower($query)
    RETURN
        coalesce(p.name, p.title, "Unknown product") AS product_name,
        coalesce(p.price, p.current_price, p.sale_price, "") AS price,
        coalesce(p.url, p.link, "") AS url,
        coalesce(b.name, "") AS brand,
        coalesce(c.name, "") AS category,
        collect(DISTINCT t.name)[0..8] AS tags,
        collect(DISTINCT {
            name: s.name,
            value: s.value
        })[0..10] AS specs
    LIMIT $limit
    """

    try:
        driver = get_neo4j_driver()

        records, _, _ = driver.execute_query(
            cypher,
            query=query,
            limit=limit,
            database_=settings.NEO4J_DATABASE,
        )

        if not records:
            return "No relevant graph facts found."

        parts = []

        for record in records:
            data = record.data()

            specs = data.get("specs") or []
            spec_text = ", ".join(
                f"{item.get('name')}: {item.get('value')}"
                for item in specs
                if item and item.get("name") and item.get("value")
            )

            tags = data.get("tags") or []
            tag_text = ", ".join(str(tag) for tag in tags if tag)

            parts.append(
                "\n".join(
                    [
                        f"Product: {data.get('product_name')}",
                        f"Brand: {data.get('brand') or 'Unknown'}",
                        f"Category: {data.get('category') or 'Unknown'}",
                        f"Price: {data.get('price') or 'Unknown'}",
                        f"URL: {data.get('url') or 'Unknown'}",
                        f"Tags: {tag_text or 'None'}",
                        f"Specs: {spec_text or 'None'}",
                    ]
                )
            )

        return "\n\n---\n\n".join(parts)

    except Exception as exc:
        log.exception("Neo4j LifeStore graph search failed: %s", exc)
        return "No relevant graph facts found."