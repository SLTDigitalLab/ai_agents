"""DA-04: images must belong to the resolved product; all I/O is mocked."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from routers import lifestore_mcp_chat as chat


def product(model):
    return {"name": f"Prolink {model} Router", "model": model,
            "url": f"https://lifestore.lk/product/prolink-{model.lower()}"}


A, B, C = [product(model) for model in ("AX100", "AX1000", "AX200")]
IA, IB, IC = [f"https://lifestore.lk/sites/default/files/{x}.jpg" for x in ("a", "b", "c")]


def doc(p, image):
    return {"text": A["name"], "score": 1.0,
            "metadata": {**p, "image_url": image}}


class ImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = chat._load_mcp_module()

    def setUp(self):
        for name, value in [("_load_image_cache", {}), ("_lookup_verified_product_image", ""),
                            ("_save_image_cache", None)]:
            patcher = patch.object(self.server, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(self.server.httpx, "get", side_effect=AssertionError("Unexpected HTTP"))
        self.http = patcher.start()
        self.addCleanup(patcher.stop)

    def merge(self, rows, docs):
        return self.server._merge_graph_and_vector_results(rows, docs, "routers", 8, allow_page_fetch=False)

    def precise(self, docs):
        with patch.object(self.server, "_graph_identity_rows", return_value=[A]), \
             patch.object(self.server, "_graph_product_search_rows", return_value=[]), \
             patch.object(self.server, "load_products", return_value=[]), \
             patch.object(self.server, "_qdrant_search_documents", return_value=docs):
            return self.server.lifestore_precise_product_lookup(A["name"])["products"][0]

    def test_precise_wrong_vector_image_rejected(self):
        self.assertEqual(self.precise([doc(B, IB)])["image_url"], "")

    def test_precise_exact_vector_image_accepted(self):
        self.assertEqual(self.precise([doc(A, IA)])["image_url"], IA)

    def test_authoritative_image_preserved(self):
        self.assertEqual(self.merge([{**A, "image_url": IA}], [doc(B, IB)])[0]["image_url"], IA)

    def test_missing_image(self):
        self.assertFalse(self.merge([A], [])[0]["image_available"])
        self.http.assert_not_called()

    def test_category_images(self):
        self.assertEqual([p["image_url"] for p in self.merge([A, B, C],
                         [doc(C, IC), doc(A, IA), doc(B, IB)])], [IA, IB, IC])

    def test_multiple_chunks(self):
        self.assertEqual([p["image_url"] for p in self.merge([A, B],
                         [doc(B, IB), doc(B, IB), doc(A, IA), doc(A, IA)])], [IA, IB])

    def test_similar_models_do_not_match(self):
        self.assertEqual(self.merge([A], [doc(B, IB)])[0]["image_url"], "")

    def test_conflicting_url_vetoes_name(self):
        self.assertEqual(self.merge([A], [doc({**A, "url": B["url"]}, IB)])[0]["image_url"], "")

    def test_conflicting_model_vetoes_url(self):
        self.assertEqual(self.merge([A], [doc({**A, "model": B["model"]}, IB)])[0]["image_url"], "")

    def test_normalized_name_without_product_url(self):
        row = {"name": "PROLINK AX100 Router"}
        self.assertEqual(self.merge([row], [doc(A, IA)])[0]["image_url"], IA)

    def test_record_image_alias(self):
        self.assertEqual(self.merge([{**A, "image": IA}], [])[0]["image_url"], IA)

    def test_page_recommendations_rejected(self):
        html = f'<main><div class="product"><img src="{IB}"></div></main>'
        self.assertEqual(self.server._extract_image_candidates_from_html(A["url"], html), [])

    def test_page_structured_identity(self):
        objects = [{"@type": "Product", "url": p["url"], "image": image}
                   for p, image in [(B, IB), (A, IA)]]
        html = '<script type="application/ld+json">' + json.dumps(objects) + '</script>'
        self.assertEqual(self.server._extract_image_candidates_from_html(A["url"], html), [IA])

    def test_legacy_cache_is_not_reused(self):
        self.assertEqual(self.server.IMAGE_CACHE_PATH.name, "lifestore_image_cache_v2.json")

    def test_verified_lookup_precedes_cache(self):
        with patch.object(self.server, "_lookup_verified_product_image", return_value=IA), \
             patch.object(self.server, "_load_image_cache", return_value={A["url"]: IB}):
            self.assertEqual(self.merge([A], [])[0]["image_url"], IA)


if __name__ == "__main__":
    unittest.main()
