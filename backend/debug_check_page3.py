"""
One-off check: what is actually on the page that used to crash the backend
(page 2 of Procurement Manual Part-I-pages-3.pdf in the SCM OneDrive folder)?
Used to craft a chat question guaranteed to surface that exact page's image.

Usage inside the backend container:
    python3 debug_check_page3.py
"""
from qdrant_client import QdrantClient, models
from core.config import settings

client = QdrantClient(url=settings.QDRANT_URL)
result = client.scroll(
    collection_name="scm_docs",
    scroll_filter=models.Filter(
        must=[
            models.FieldCondition(
                key="metadata.source",
                match=models.MatchValue(value="Procurement Manual Part-I-pages-3.pdf"),
            ),
            models.FieldCondition(
                key="metadata.type",
                match=models.MatchValue(value="visual_description"),
            ),
        ]
    ),
    limit=20,
)
points, _ = result
print(f"Found {len(points)} visual points")
for p in points:
    md = p.payload.get("metadata", {})
    content = p.payload.get("page_content", "")
    print("page", md.get("page_number"), "-", content[:300])
