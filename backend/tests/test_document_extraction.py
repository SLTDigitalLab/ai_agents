"""Local fixtures and mocked OpenAI responses; no external service calls."""
import ast
import json
import logging
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import fitz
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.document_extraction import OpenAIPageExtractor, difficulty_reasons, poor_text


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "source.pdf"
        with fitz.open() as pdf:
            for text in ("A readable policy paragraph with enough content for ordinary extraction. " * 3,
                         "Poor scan"):
                page = pdf.new_page()
                page.insert_text((40, 50), text)
            pdf.save(self.path)
        self.local = [
            Document(page_content="A readable policy paragraph with enough content for ordinary extraction. " * 3,
                     metadata={"page_number": 1, "source": str(self.path)}),
            Document(page_content="Poor scan", metadata={"page_number": 2, "source": str(self.path)}),
        ]
        self.client = Mock()
        self.client.responses.create.return_value = SimpleNamespace(
            status="completed", output_text=json.dumps({"blocks": [
                {"kind": "text", "text": "Recovered policy text", "table_html": ""},
                {"kind": "table", "text": "Amount | 100", "table_html": "<table><tr><th>Amount</th><td>100</td></tr></table>"},
                {"kind": "visual", "text": "An arrow connects A to B", "table_html": ""},
            ], "needs_review": False}))
        self.settings = SimpleNamespace(
            EXTRACTION_OPENAI_ENABLED=True, EXTRACTION_OPENAI_MODEL="gpt-4.1",
            EXTRACTION_OPENAI_TIMEOUT_SECONDS=90, OPENAI_API_KEY="test-key",
        )
        self.extractor = OpenAIPageExtractor(self.settings, self.client)

    def test_replaces_only_difficult_page_and_preserves_page_and_table(self):
        result = self.extractor.improve(self.path, self.local)
        self.client.responses.create.assert_called_once()
        self.assertIs(result[0], self.local[0])
        self.assertNotIn(self.local[1], result)
        self.assertEqual([d.metadata["page_number"] for d in result], [1, 2, 2, 2])
        self.assertIn("text_as_html", result[2].metadata)
        self.assertTrue(result[3].page_content.startswith("[Visual description]"))
        call = self.client.responses.create.call_args.kwargs
        self.assertFalse(call["store"])
        self.assertEqual(call["model"], "gpt-4.1")
        self.assertTrue(call["text"]["format"]["strict"])
        self.assertEqual(call["input"][0]["content"][1]["detail"], "high")

    def test_timeout_keeps_local_content(self):
        self.client.responses.create.side_effect = TimeoutError()
        result = self.extractor.improve(self.path, self.local)
        self.assertEqual(result, self.local)
        self.assertTrue(result[1].metadata["extraction_needs_review"])

    def test_incomplete_empty_invalid_and_refused_output_keeps_local(self):
        for response in (
            SimpleNamespace(status="incomplete", output_text="{}"),
            SimpleNamespace(status="completed", output_text=""),
            SimpleNamespace(status="completed", output_text="not json"),
            SimpleNamespace(status="completed", output_text='{"blocks": [], "needs_review": false}'),
        ):
            with self.subTest(response=response):
                self.client.responses.create.return_value = response
                self.assertEqual(self.extractor.improve(self.path, self.local), self.local)

    def test_disabled_and_missing_key_make_no_calls(self):
        self.settings.EXTRACTION_OPENAI_ENABLED = False
        self.assertIs(self.extractor.improve(self.path, self.local), self.local)
        self.client.responses.create.assert_not_called()
        self.settings.EXTRACTION_OPENAI_ENABLED = True
        self.settings.OPENAI_API_KEY = None
        with patch("openai.OpenAI") as factory:
            self.assertIs(OpenAIPageExtractor(self.settings).improve(self.path, self.local), self.local)
            factory.assert_not_called()

    def test_missing_page_metadata_preserves_document(self):
        self.local[0].metadata.clear()
        self.assertIs(self.extractor.improve(self.path, self.local), self.local)
        self.client.responses.create.assert_not_called()

    def test_recovers_when_local_extraction_is_empty(self):
        result = self.extractor.improve(self.path, [])
        self.assertEqual(self.client.responses.create.call_count, 2)
        self.assertEqual({d.metadata["page_number"] for d in result}, {1, 2})

    def test_image_ocr_fallback_and_clean_image_skip(self):
        image_path = self.path.with_suffix(".png")
        with fitz.open(self.path) as pdf:
            pdf[0].get_pixmap().save(image_path)
        result = self.extractor.improve(image_path, [])
        self.assertEqual(result[0].metadata["page_number"], 1)
        self.client.responses.create.reset_mock()
        self.assertIs(self.extractor.improve(image_path, [self.local[0]])[0], self.local[0])
        self.client.responses.create.assert_not_called()

    def test_quality_signals_do_not_reject_sinhala(self):
        self.assertFalse(poor_text("සිංහල භාෂාවෙන් ලියන ලද ලේඛනයකි " * 10))
        self.assertTrue(poor_text("broken \ufffd" * 25))
        self.assertTrue(poor_text("(cid:123)" * 20))
        table = Document(page_content="Table " * 30, metadata={"category": "Table"})
        self.assertIn("unstructured_table", difficulty_reasons(table.page_content, [table]))
        table.metadata["text_as_html"] = "<table></table>"
        self.assertEqual(difficulty_reasons(table.page_content, [table]), [])

    def test_pdf_table_is_selected_even_with_plenty_of_text(self):
        with fitz.open() as pdf:
            page = pdf.new_page()
            for x in (40, 180, 320):
                page.draw_line((x, 100), (x, 180))
            for y in (100, 140, 180):
                page.draw_line((40, y), (320, y))
            for point, text in (((50, 120), "Item"), ((190, 120), "Amount"),
                                ((50, 160), "A"), ((190, 160), "100")):
                page.insert_text(point, text)
            reasons = difficulty_reasons(self.local[0].page_content, [self.local[0]], page)
            self.assertIn("table_without_structure", reasons)

    def test_large_illustration_but_not_successfully_ocrd_scan_is_selected(self):
        page = Mock()
        page.rect = fitz.Rect(0, 0, 600, 800)
        page.get_image_info.return_value = [{"bbox": (0, 0, 600, 400)}]
        page.find_tables.return_value.tables = []
        page.get_text.return_value = self.local[0].page_content
        self.assertIn("large_illustration", difficulty_reasons(self.local[0].page_content, [self.local[0]], page))
        page.get_text.return_value = ""
        self.assertEqual(difficulty_reasons(self.local[0].page_content, [self.local[0]], page), [])

    def test_loader_calls_fallback_after_ocr_failure_before_splitting(self):
        # Load the actual method without initializing Qdrant, BM25, or models.
        path = Path(__file__).resolve().parents[1] / "services/ingestion.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "IngestionService")
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_load_and_chunk_file")
        namespace = {"Path": Path, "Document": Document, "settings": self.settings,
                     "log": logging.getLogger("test")}
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), "exec"), namespace)
        helper = SimpleNamespace(
            _load_with_strategy=Mock(side_effect=RuntimeError("OCR failed")),
            _is_table_doc=Mock(return_value=False), _derive_section_heading=Mock(return_value="Policy"),
            recursive_splitter=RecursiveCharacterTextSplitter(chunk_size=1800, chunk_overlap=300),
        )
        recovered = Document(page_content="Recovered content " * 300, metadata={"page_number": 1})
        with patch("services.document_extraction.OpenAIPageExtractor") as factory:
            factory.return_value.improve.return_value = [recovered]
            result = namespace["_load_and_chunk_file"](helper, self.path.with_suffix(".png"))
        self.assertGreater(len(result), 1)
        self.assertTrue(all(d.metadata["page_number"] == 1 for d in result))
        self.assertTrue(all("Policy" in d.page_content for d in result))


if __name__ == "__main__":
    unittest.main()
