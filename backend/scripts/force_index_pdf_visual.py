"""Force-index one known diagram/chart page as Visual RAG evidence.

Example:
    python scripts/force_index_pdf_visual.py \
      storage/raw_documents/askhr/Performance\ Management\ Procedure_06_23July2025.pdf \
      hr 6
"""

import sys
import uuid
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import fitz
from langchain_qdrant import QdrantVectorStore, RetrievalMode
from qdrant_client import models

from core.config import agent_collection_name, evidence_storage_dir
from core.llm import get_embedding_model
from domain.tools.rag_tools import _sparse_embeddings
from services.ingestion import ingestion_service
from services.visual_extractor import (
    _write_audit_record,
    analyze_image_with_gpt4o,
    create_vector_document,
    encode_image,
    save_visual_record,
)

FORCED_VISUAL_PROMPT = """This page is known to contain an important business diagram,
flowchart, chart, framework, or structured visual. Analyze the visual in detail.

1. State the visual title.
2. Transcribe all readable labels, stages, dates, values, and decision points.
3. Explain the flow, hierarchy, relationships, or trend step by step.
4. Describe the purpose of the visual in concise business terms.

Do not reply with NO_VISUAL_CONTENT."""


def force_index(pdf_path: Path, agent_id: str, page_number: int) -> None:
    if not pdf_path.is_file():
        raise FileNotFoundError(pdf_path)

    document = fitz.open(pdf_path)
    try:
        if page_number < 1 or page_number > len(document):
            raise ValueError(f"Page must be between 1 and {len(document)}")

        doc_id = pdf_path.stem
        evidence_dir = evidence_storage_dir()
        image_path = evidence_dir / f"{doc_id}_page_{page_number}.png"
        document.load_page(page_number - 1).get_pixmap(dpi=150).save(str(image_path))
    finally:
        document.close()

    description = analyze_image_with_gpt4o(str(image_path), FORCED_VISUAL_PROMPT)
    if not description or "NO_VISUAL_CONTENT" in description:
        _write_audit_record(
            doc_id,
            pdf_path.name,
            page_number,
            "skipped",
            "forced_visual_analysis_failed",
            image_path=str(image_path),
        )
        raise RuntimeError("GPT-4o did not return a usable visual description")

    base64_image = encode_image(str(image_path))
    save_visual_record(doc_id, pdf_path.name, page_number, image_path, description)
    visual_doc = create_vector_document(
        doc_id, pdf_path.name, page_number, str(image_path), description, base64_image
    )

    collection_name = agent_collection_name(agent_id)
    ingestion_service._ensure_collection_exists(collection_name)
    ingestion_service.client.delete(
        collection_name=collection_name,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="metadata.doc_id",
                        match=models.MatchValue(value=doc_id),
                    ),
                    models.FieldCondition(
                        key="metadata.page_number",
                        match=models.MatchValue(value=page_number),
                    ),
                    models.FieldCondition(
                        key="metadata.type",
                        match=models.MatchValue(value="visual_description"),
                    ),
                ]
            )
        ),
    )

    vector_store = QdrantVectorStore(
        client=ingestion_service.client,
        collection_name=collection_name,
        embedding=get_embedding_model(),
        sparse_embedding=_sparse_embeddings,
        retrieval_mode=RetrievalMode.HYBRID,
        vector_name="dense",
        sparse_vector_name="sparse",
    )
    point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{agent_id}:{doc_id}:{page_number}:visual"))
    vector_store.add_documents([visual_doc], ids=[point_id])
    _write_audit_record(
        doc_id,
        pdf_path.name,
        page_number,
        "indexed",
        "forced_visual_description_created",
        image_path=str(image_path),
    )
    print(f"Indexed forced visual: {pdf_path.name}, page {page_number}, collection {collection_name}")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit("Usage: python scripts/force_index_pdf_visual.py <pdf_path> <agent_id> <page_number>")
    force_index(Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]))