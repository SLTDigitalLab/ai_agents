"""Checks for the production Visual RAG relevance gate."""

import json
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, Mock, patch

from langchain_core.documents import Document
import fitz

from domain.tools import rag_tools
from domain.tools import visual_evidence
from services import visual_extractor


def candidate(point_id: str, url: str, description: str) -> dict:
    return {
        "point_id": point_id,
        "description": description,
        "item": {"type": "image", "url": url, "source": "policy.pdf", "page": 2},
    }


class CosineTests(unittest.TestCase):
    def test_cosine_rejects_invalid_vectors(self):
        self.assertEqual(visual_evidence._cosine_similarity([1, 0], [1]), 0)
        self.assertEqual(visual_evidence._cosine_similarity([0, 0], [1, 0]), 0)

    def test_pdf_extractor_creates_a_searchable_visual_document(self):
        with TemporaryDirectory() as temp_name:
            temp_dir = Path(temp_name)
            pdf_path = temp_dir / "policy.pdf"
            pdf = fitz.open()
            page = pdf.new_page()
            page.insert_text((72, 72), "Leave approval flow")
            pdf.save(pdf_path)
            pdf.close()
            storage = temp_dir / "evidence"
            storage.mkdir()
            audit = storage / "audits"
            audit.mkdir()
            with patch.object(visual_extractor, "STORAGE_DIR", storage), patch.object(
                visual_extractor, "AUDIT_DIR", audit
            ), patch.object(
                visual_extractor.settings, "VISUAL_AUDIT_ALL_PDF_PAGES", True
            ), patch.object(
                visual_extractor, "analyze_image_with_gpt4o", return_value="Title: Leave approval flow\nTopic: leave applications"
            ):
                _, documents = visual_extractor.process_pdf_visuals(str(pdf_path), "safe-id")

            self.assertEqual(len(documents), 1)
            self.assertEqual(documents[0].metadata["type"], "visual_description")
            self.assertIn("Leave approval flow", documents[0].page_content)
            self.assertTrue(Path(documents[0].metadata["image_path"]).is_file())


class VisualEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_diagram_request_searches_visuals_when_text_search_is_empty(self):
        doc = Document(
            page_content="Title: Leave approval flow",
            metadata={"_id": "visual-1", "type": "visual_description", "source": "policy.pdf", "image_path": "/app/storage/evidence/flow.png"},
        )
        vector_store = Mock(asimilarity_search=AsyncMock(side_effect=[[], [doc]]))
        thread_id = "visual-only-test"
        rag_tools.clear_thread_evidence(thread_id)
        with patch.object(rag_tools.settings, "KB_REMOTE_URL", None), patch.object(
            rag_tools.settings, "VISUAL_RAG_ENABLED", True
        ), patch.object(rag_tools, "get_embedding_model", return_value=Mock()), patch.object(
            rag_tools, "QdrantClient", return_value=Mock(collection_exists=Mock(return_value=True))
        ), patch.object(rag_tools, "QdrantVectorStore", return_value=vector_store), patch.object(
            rag_tools, "select_relevant_images", AsyncMock(side_effect=lambda query, candidates, *args: [candidates[0]["item"]])
        ), patch.object(Path, "is_file", return_value=True):
            context = await rag_tools._search_qdrant_knowledge_base(
                "Show the leave flowchart", "hr", thread_id=thread_id
            )

        self.assertIn("Leave approval flow", context)
        self.assertEqual(len(rag_tools.consume_thread_evidence(thread_id)), 1)
        self.assertEqual(vector_store.asimilarity_search.await_count, 2)

    async def test_retrieval_uses_visual_descriptions_instead_of_legacy_crops(self):
        legacy_image = {"type": "image", "url": "/api/v1/evidence/images/legacy.png"}
        doc = Document(
            page_content="Leave application flow and approval steps",
            metadata={"_id": "point-1", "source": "policy.pdf", "link": "https://example.test/policy", "type": "visual_description", "image_path": "/app/storage/evidence/flow.png"},
        )
        legacy_doc = Document(
            page_content="General leave policy",
            metadata={"_id": "point-2", "source": "policy.pdf", "evidence": [legacy_image]},
        )
        vector_store = Mock(asimilarity_search=AsyncMock(return_value=[doc, legacy_doc]))
        client = Mock(collection_exists=Mock(return_value=True))
        reviewer = AsyncMock(side_effect=lambda query, candidates, *args: [candidates[0]["item"]])
        thread_id = "visual-evidence-test"
        rag_tools.clear_thread_evidence(thread_id)

        with patch.object(rag_tools.settings, "KB_REMOTE_URL", None), patch.object(
            rag_tools.settings, "VISUAL_RAG_ENABLED", True
        ), patch.object(rag_tools, "get_embedding_model", return_value=Mock()), patch.object(
            rag_tools, "QdrantClient", return_value=client
        ), patch.object(rag_tools, "QdrantVectorStore", return_value=vector_store), patch.object(
            rag_tools, "select_relevant_images", reviewer
        ), patch.object(Path, "is_file", return_value=True
        ):
            answer_context = await rag_tools._search_qdrant_knowledge_base(
                "How do I apply for leave?", "hr", thread_id=thread_id
            )

        self.assertIn("Leave application flow", answer_context)
        images = rag_tools.consume_thread_evidence(thread_id)
        self.assertEqual(len(images), 1)
        self.assertTrue(images[0]["url"].endswith("/flow.png"))
        self.assertEqual(reviewer.await_count, 1)

    async def test_only_similar_and_directly_relevant_image_survives(self):
        candidates = [
            candidate("a", "/api/v1/evidence/images/a.png", "Leave application flow"),
            candidate("b", "/api/v1/evidence/images/b.png", "Expense claim flow"),
        ]
        embeddings = Mock(embed_query=Mock(return_value=[1.0, 0.0]))
        client = Mock(retrieve=Mock(return_value=[
            SimpleNamespace(id="a", vector={"dense": [0.9, 0.1]}),
            SimpleNamespace(id="b", vector={"dense": [0.0, 1.0]}),
        ]))
        review = SimpleNamespace(reviews=[SimpleNamespace(candidate_index=0, directly_relevant=True)])
        model = SimpleNamespace(ainvoke=AsyncMock(return_value=review))

        with patch.object(visual_evidence, "_model", return_value=model), patch.object(
            visual_evidence.settings, "VISUAL_EVIDENCE_MIN_SCORE", 0.45
        ):
            selected = await visual_evidence.select_relevant_images(
                "How do I apply for leave?", candidates, "hr_docs", client, embeddings
            )

        self.assertEqual(selected, [candidates[0]["item"]])
        review_payload = json.loads(model.ainvoke.call_args.args[0][1][1])
        self.assertEqual(len(review_payload["candidates"]), 1)

    async def test_model_failure_omits_images_without_breaking_text(self):
        embeddings = Mock(embed_query=Mock(return_value=[1.0, 0.0]))
        client = Mock(retrieve=Mock(return_value=[SimpleNamespace(id="a", vector=[1.0, 0.0])]))
        model = SimpleNamespace(ainvoke=AsyncMock(side_effect=RuntimeError("provider unavailable")))

        with patch.object(visual_evidence, "_model", return_value=model):
            selected = await visual_evidence.select_relevant_images(
                "leave", [candidate("a", "/api/v1/evidence/images/a.png", "Leave flow")],
                "hr_docs", client, embeddings,
            )

        self.assertEqual(selected, [])


if __name__ == "__main__":
    unittest.main()
