"""
Ingest REAL historical tickets (not hand-written KB summaries) into Qdrant,
as an alternative reference corpus for domain.helpdesk.tools.ticket_example_tools.

Instead of the 75-row hand-written Category_Knowledge_Base_v2.xlsx, this
embeds each TRAINING-split ticket from Incidents_Feb.xlsx individually, so
classify_ticket_category_examples() can retrieve the most similar REAL past
tickets (and their already-known correct category) for an incoming message.

Only the TRAIN portion of the same train/test split (see _split() below) is
embedded here, so held-out test rows are never leaked into this retrieval
corpus (which would otherwise let the search find a ticket matching
itself and inflate any eval number falsely).

Usage (from /app inside the backend container):
    python domain/helpdesk/scripts/ingest_ticket_examples.py
    python domain/helpdesk/scripts/ingest_ticket_examples.py --recreate
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
from domain.helpdesk.tools.ticket_example_tools import TICKET_EXAMPLES_COLLECTION  # noqa: E402

TEST_FRACTION = 0.20
MIN_TEST_ROWS = 10


def _split(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split by main_category so held-out test rows are excluded from ingestion."""
    train_parts = []
    test_parts = []
    for _category, group in df.groupby("main_category"):
        group = group.sample(frac=1.0, random_state=42)
        n_test = max(MIN_TEST_ROWS, round(len(group) * TEST_FRACTION))
        n_test = min(n_test, len(group) - 1) if len(group) > 1 else 0
        test_parts.append(group.iloc[:n_test])
        train_parts.append(group.iloc[n_test:])
    return pd.concat(train_parts, ignore_index=True), pd.concat(test_parts, ignore_index=True)


def _ensure_collection(client: QdrantClient, recreate: bool) -> None:
    exists = client.collection_exists(TICKET_EXAMPLES_COLLECTION)

    if exists and recreate:
        print(f"--recreate set: deleting existing collection '{TICKET_EXAMPLES_COLLECTION}'")
        client.delete_collection(TICKET_EXAMPLES_COLLECTION)
        exists = False

    if exists:
        print(f"Collection '{TICKET_EXAMPLES_COLLECTION}' already exists — reusing it.")
        return

    print(f"Creating collection '{TICKET_EXAMPLES_COLLECTION}'...")
    client.create_collection(
        collection_name=TICKET_EXAMPLES_COLLECTION,
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="domain/helpdesk/data/Incidents_Feb.xlsx")
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="Drop and recreate the collection before ingesting.",
    )
    parser.add_argument(
        "--test-output",
        default=None,
        help="If set, also write the held-out test split to this .xlsx path "
        "(message/true_category/true_sub_category columns).",
    )
    args = parser.parse_args()

    df = pd.read_excel(args.input)
    df["main_category"] = df["INCIDENT_TYPE_CD"].str.strip().str.replace(r"\s+", " ", regex=True)
    df["sub_category"] = df["INC_SUB_TYPE_CD"].str.strip().str.replace(r"\s+", " ", regex=True)
    df["text"] = df["DESCRIPTION"].astype(str).str.strip()
    df = df[df["text"].str.len() > 0]

    train_df, test_df = _split(df)
    print(f"Train rows to embed: {len(train_df)} (held-out test rows excluded: {len(test_df)})")

    if args.test_output:
        test_out = test_df[["text", "main_category", "sub_category"]].rename(
            columns={"text": "message", "main_category": "true_category", "sub_category": "true_sub_category"}
        )
        test_out.to_excel(args.test_output, index=False)
        print(f"Wrote {len(test_out)} held-out test rows to {args.test_output}")

    docs = [
        Document(
            page_content=row["text"],
            metadata={"main_category": row["main_category"], "sub_category": row["sub_category"]},
        )
        for _, row in train_df.iterrows()
    ]

    client = QdrantClient(url=settings.QDRANT_URL)
    _ensure_collection(client, recreate=args.recreate)

    vector_store = QdrantVectorStore(
        client=client,
        collection_name=TICKET_EXAMPLES_COLLECTION,
        embedding=get_embedding_model(),
        sparse_embedding=FastEmbedSparse(model_name="Qdrant/bm25"),
        retrieval_mode=RetrievalMode.HYBRID,
        vector_name="dense",
        sparse_vector_name="sparse",
    )

    batch_size = 200
    total_ingested = 0
    for i in range(0, len(docs), batch_size):
        batch = docs[i : i + batch_size]
        ids = vector_store.add_documents(batch)
        total_ingested += len(ids)
        print(f"  ingested {total_ingested}/{len(docs)}")

    print(f"\nIngested {total_ingested} real ticket example(s) into '{TICKET_EXAMPLES_COLLECTION}'")


if __name__ == "__main__":
    main()
