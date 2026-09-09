"""
One-off ingestion of the helpdesk category knowledge base into Qdrant.

Reads domain/helpdesk/data/Category_Knowledge_Base_v2.xlsx (one row per
main_category/sub_category pair, with a pre-composed Search_Text column
combining intent, description, customer expressions, keywords, and example
tickets) and embeds each row individually into the
domain.helpdesk.tools.category_kb_tools.CATEGORY_KB_COLLECTION Qdrant
collection, so classify_ticket_category_vector() (see
domain/helpdesk/pipeline/category_classification.py) can retrieve the
top-k most similar categories for an incoming ticket message instead of
prompting the LLM with the full category list.

Must run inside the backend container/environment — it uses the same
embedding model factory and Qdrant client as the rest of the app.

Usage (from /app inside the backend container):
    python domain/helpdesk/scripts/ingest_category_kb.py
    python domain/helpdesk/scripts/ingest_category_kb.py --recreate
    python domain/helpdesk/scripts/ingest_category_kb.py --input domain/helpdesk/data/Category_Knowledge_Base_v2.xlsx
"""

import argparse
import sys

import pandas as pd
from langchain_core.documents import Document
from langchain_qdrant import QdrantVectorStore, FastEmbedSparse, RetrievalMode
from qdrant_client import QdrantClient, models

sys.path.insert(0, "/app")

from core.config import settings  # noqa: E402
from core.llm import get_embedding_model  # noqa: E402
from domain.helpdesk.tools.category_kb_tools import CATEGORY_KB_COLLECTION  # noqa: E402

# Every non-Search_Text column in the KB becomes row metadata, keyed in
# lowercase so classify_ticket_category_vector() can read it uniformly
# regardless of the source spreadsheet's header casing.
METADATA_COLUMNS = [
    "Main_Category",
    "Sub_Category",
    "Total_Incidents",
    "Intent",
    "Description",
    "Customer_Expressions",
    "Internal_Expressions",
    "Symptoms",
    "Keywords",
    "Similar_Categories",
    "Do_Not_Use_When",
    "Representative_Examples",
    "Common_Resolution",
]


def _ensure_collection(client: QdrantClient, recreate: bool) -> None:
    exists = client.collection_exists(CATEGORY_KB_COLLECTION)

    if exists and recreate:
        print(f"--recreate set: deleting existing collection '{CATEGORY_KB_COLLECTION}'")
        client.delete_collection(CATEGORY_KB_COLLECTION)
        exists = False

    if exists:
        print(f"Collection '{CATEGORY_KB_COLLECTION}' already exists — reusing it.")
        return

    print(f"Creating collection '{CATEGORY_KB_COLLECTION}'...")
    client.create_collection(
        collection_name=CATEGORY_KB_COLLECTION,
        vectors_config={
            "dense": models.VectorParams(
                size=settings.EMBEDDING_DIMENSIONS,
                distance=models.Distance.COSINE,
            ),
        },
        sparse_vectors_config={
            "sparse": models.SparseVectorParams(
                index=models.SparseIndexParams(on_disk=False),
            ),
        },
    )


def _build_documents(df: pd.DataFrame) -> list[Document]:
    missing = {"Search_Text", *METADATA_COLUMNS} - set(df.columns)
    if missing:
        raise SystemExit(f"Input file is missing expected column(s): {missing}")

    docs: list[Document] = []
    for _, row in df.iterrows():
        search_text = str(row["Search_Text"] or "").strip()
        if not search_text:
            continue
        metadata = {col.lower(): row[col] for col in METADATA_COLUMNS}
        docs.append(Document(page_content=search_text, metadata=metadata))
    return docs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        default="domain/helpdesk/data/Category_Knowledge_Base_v2.xlsx",
        help="Path to the category knowledge base spreadsheet",
    )
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="Drop and recreate the collection before ingesting, so stale "
        "rows from a previous version of the KB don't linger.",
    )
    args = parser.parse_args()

    df = pd.read_excel(args.input, sheet_name="Knowledge Base")
    docs = _build_documents(df)
    print(f"Loaded {len(docs)} category row(s) from {args.input}")

    client = QdrantClient(url=settings.QDRANT_URL)
    _ensure_collection(client, recreate=args.recreate)

    vector_store = QdrantVectorStore(
        client=client,
        collection_name=CATEGORY_KB_COLLECTION,
        embedding=get_embedding_model(),
        sparse_embedding=FastEmbedSparse(model_name="Qdrant/bm25"),
        retrieval_mode=RetrievalMode.HYBRID,
        vector_name="dense",
        sparse_vector_name="sparse",
    )
    ids = vector_store.add_documents(docs)

    print(f"Ingested {len(ids)} point(s) into '{CATEGORY_KB_COLLECTION}'")


if __name__ == "__main__":
    main()
