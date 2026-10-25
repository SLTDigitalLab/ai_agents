"""Checks for the production Visual RAG relevance gate."""

import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from langchain_core.documents import Document

from domain.tools import rag_tools
from domain.tools import visual_evidence


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


class VisualEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_retrieval_keeps_text_and_only_approved_image(self):
        image = {"type": "image", "url": "/api/v1/evidence/images/flow.png"}
        doc = Document(
            page_content="Leave application flow and approval steps",
            metadata={"_id": "point-1", "source": "policy.pdf", "link": "https://example.test/policy", "evidence": [image]},
        )
        vector_store = Mock(asimilarity_search=AsyncMock(return_value=[doc]))
        client = Mock(collection_exists=Mock(return_value=True))
        reviewer = AsyncMock(return_value=[{**image, "source": "policy.pdf", "link": "https://example.test/policy"}])
        thread_id = "visual-evidence-test"
        rag_tools.clear_thread_evidence(thread_id)

        with patch.object(rag_tools.settings, "KB_REMOTE_URL", None), patch.object(
            rag_tools.settings, "VISUAL_EVIDENCE_RELEVANCE_ENABLED", True
        ), patch.object(rag_tools, "get_embedding_model", return_value=Mock()), patch.object(
            rag_tools, "QdrantClient", return_value=client
        ), patch.object(rag_tools, "QdrantVectorStore", return_value=vector_store), patch.object(
            rag_tools, "select_relevant_images", reviewer
        ):
            answer_context = await rag_tools._search_qdrant_knowledge_base(
                "How do I apply for leave?", "hr", thread_id=thread_id
            )

        self.assertIn("Leave application flow", answer_context)
        self.assertEqual(len(rag_tools.consume_thread_evidence(thread_id)), 1)
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
