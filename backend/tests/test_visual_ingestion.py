"""Verify that failed visual re-ingestion preserves the previous Qdrant data."""

import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from langchain_core.documents import Document
from qdrant_client import QdrantClient, models

from services.ingestion import IngestionService
from core.config import settings


class _Store:
    def __init__(self, client, fail_after_write=False):
        self.client = client
        self.fail_after_write = fail_after_write

    def add_documents(self, docs):
        self.client.upsert(
            collection_name="visual-replacement-test",
            points=[models.PointStruct(
                id=str(uuid.uuid4()),
                vector=[1.0, 0.0],
                payload={"metadata": doc.metadata, "page_content": doc.page_content},
            ) for doc in docs],
        )
        if self.fail_after_write:
            raise RuntimeError("simulated partial Qdrant write")


class VisualReplacementTests(unittest.TestCase):
    def test_missing_vision_key_fails_before_replacing_points(self):
        service = IngestionService.__new__(IngestionService)
        with patch.object(settings, "VISUAL_RAG_ENABLED", True), patch.object(
            settings, "OPENAI_API_KEY", None
        ):
            with self.assertRaisesRegex(RuntimeError, "OPENAI_API_KEY"):
                service._extract_visual_documents(Path("policy.pdf"), "file-1")

    def test_vision_failure_fails_before_replacing_points(self):
        service = IngestionService.__new__(IngestionService)
        with patch.object(settings, "VISUAL_RAG_ENABLED", True), patch.object(
            settings, "OPENAI_API_KEY", "test-key"
        ), patch("services.visual_extractor.process_document_visuals", side_effect=RuntimeError("vision unavailable")):
            with self.assertRaisesRegex(RuntimeError, "vision unavailable"):
                service._extract_visual_documents(Path("policy.pdf"), "file-1")

    def test_visual_assets_get_new_names_for_each_attempt(self):
        service = IngestionService.__new__(IngestionService)
        visual_ids = []

        def extract(_path, visual_id):
            visual_ids.append(visual_id)
            return [], [Document(page_content="diagram", metadata={"image_base64": "bytes"})]

        with patch.object(settings, "VISUAL_RAG_ENABLED", True), patch.object(
            settings, "OPENAI_API_KEY", "test-key"
        ), patch("services.visual_extractor.process_document_visuals", side_effect=extract):
            first = service._extract_visual_documents(Path("policy.pdf"), "unsafe/item-id")
            service._extract_visual_documents(Path("policy.pdf"), "unsafe/item-id")

        self.assertEqual(len(visual_ids), 2)
        self.assertNotEqual(visual_ids[0], visual_ids[1])
        self.assertNotIn("/", visual_ids[0])
        self.assertNotIn("image_base64", first[0].metadata)

    def setUp(self):
        self.client = QdrantClient(":memory:")
        self.client.create_collection(
            collection_name="visual-replacement-test",
            vectors_config=models.VectorParams(size=2, distance=models.Distance.COSINE),
        )
        self.client.upsert(
            collection_name="visual-replacement-test",
            points=[models.PointStruct(
                id=str(uuid.uuid4()),
                vector=[1.0, 0.0],
                payload={"metadata": {"onedrive_id": "file-1"}, "page_content": "old answer"},
            )],
        )
        self.service = IngestionService.__new__(IngestionService)
        self.service.client = self.client

    def _texts(self):
        points, _ = self.client.scroll(collection_name="visual-replacement-test", limit=10)
        return [point.payload["page_content"] for point in points]

    def _replace(self, fail_after_write=False):
        doc = Document(page_content="new answer", metadata={"onedrive_id": "file-1"})
        self.service._replace_file_vectors(
            _Store(self.client, fail_after_write), "visual-replacement-test",
            "file-1", "policy.pdf", [doc],
        )

    def test_success_removes_only_old_points(self):
        self._replace()
        self.assertEqual(self._texts(), ["new answer"])

    def test_partial_write_failure_preserves_old_points(self):
        with self.assertRaisesRegex(RuntimeError, "partial"):
            self._replace(fail_after_write=True)
        self.assertEqual(self._texts(), ["old answer"])


if __name__ == "__main__":
    unittest.main()
