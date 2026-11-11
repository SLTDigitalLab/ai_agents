"""CB-06 regression tests. All external boundaries are mocked."""
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from routers import lifestore_mcp_chat as chat
from services import lifestore_availability as live
from services import lifestore_memory as memory
from lifestore_memory_test_support import install_memory_database

URL = "https://lifestore.lk/product/siyol-pocket-mi-fi-router"
PRODUCT = {"name": "SIYOL Pocket Mi-Fi Router", "product_id": "SIYOL",
           "url": URL, "stock": 1, "stock_status": "in_stock", "product_type": "Router"}


def page(quantity=False):
    return httpx.Response(200, headers={"content-type": "text/html"},
                          request=httpx.Request("GET", URL), text=
                          "<html><div>Home</div><div>Products</div><h1>SIYOL Pocket Mi-Fi Router</h1>"
                          "<p>Rs. 5,000</p>" + ("<label>Quantity</label>" if quantity else "") +
                          "<h2>Product Description :</h2><p>A router</p>"
                          "<h2>Related Products</h2><label>Quantity</label></html>")


class AvailabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = chat._load_mcp_module()

    def setUp(self):
        install_memory_database(self)
        memory.clear_conversation("cb06")

    def run_chat(self, message, mode, query="SIYOL Pocket Mi-Fi Router"):
        llm = Mock()
        llm.invoke.return_value = SimpleNamespace(content=json.dumps({
            "answer_mode": mode, "product_query": query, "desired_product_count": 2}))
        with patch.object(chat, "_get_lifestore_openai_llm", return_value=llm), \
             patch.object(chat, "_maybe_summarize_conversation"):
            return chat.lifestore_mcp_chat(chat.LifeStoreMCPChatRequest(message=message, thread_id="cb06"))

    def test_named_live_overrides_stale_graph(self):
        with patch.object(self.server, "_graph_identity_rows", return_value=[PRODUCT]), \
             patch.object(self.server, "_qdrant_search_documents", return_value=[]), \
             patch.object(live.httpx, "get", return_value=page()) as get:
            result = self.run_chat("Is SIYOL Pocket Mi-Fi Router available?", "availability")
        self.assertEqual(result["answer_plan"]["answer_mode"], "availability")
        self.assertEqual(result["products"][0]["stock_status"], "out_of_stock")
        self.assertTrue(result["products"][0]["availability_verified"])
        self.assertIn("out of stock", result["answer"])
        get.assert_called_once()
        self.assertEqual(get.call_args.args, (URL,))

    def test_nonavailability_never_requests_page(self):
        with patch.object(self.server, "_graph_product_search_rows", return_value=[PRODUCT]), \
             patch.object(self.server, "_graph_identity_rows", return_value=[PRODUCT]), \
             patch.object(self.server, "_qdrant_search_documents", return_value=[]), \
             patch.object(live.httpx, "get") as get:
            for message, mode in [("Show me routers", "category_browse"),
                                  ("Tell me about SIYOL Pocket Mi-Fi Router", "single_product"),
                                  ("What are its features?", "single_product")]:
                result = self.run_chat(message, mode)
                self.assertTrue(result["products"])
            get.assert_not_called()

    def test_browse_then_first_and_plural(self):
        other = {**PRODUCT, "name": "Router B", "product_id": "B", "url": URL + "-b"}
        with patch.object(self.server, "_graph_product_search_rows", return_value=[PRODUCT, other]), \
             patch.object(self.server, "_qdrant_search_documents", return_value=[]):
            self.run_chat("Show me routers", "category_browse", "routers")
        with patch.object(live.httpx, "get", return_value=page(True)) as get:
            result = self.run_chat("Is the first one available?", "availability", "first one")
            self.assertEqual(result["products"][0]["url"], URL)
            get.assert_called_once()
        with patch.object(live.httpx, "get", return_value=page()) as get:
            result = self.run_chat("Are these available?", "availability", "these")
            self.assertEqual(len(result["products"]), 2)
            self.assertEqual({c.args[0] for c in get.call_args_list}, {URL, URL + "-b"})

    def test_timeout_no_stale_fallback(self):
        with patch.object(self.server, "lifestore_precise_product_lookup", return_value={"products": [PRODUCT]}), \
             patch.object(live.httpx, "get", side_effect=httpx.ReadTimeout("slow")):
            result = self.run_chat("Is SIYOL Pocket Mi-Fi Router available?", "availability")
        self.assertFalse(result["products"][0]["availability_verified"])
        self.assertIsNone(result["products"][0]["stock"])
        self.assertIn("couldn't verify", result["answer"])
        self.assertNotIn("in stock", result["answer"])

    def test_bad_urls_never_fetch(self):
        with patch.object(live.httpx, "get") as get:
            for url in [None, "", "https://example.com/product/a", "https://lifestore.lk/products",
                        "https://lifestore.lk@evil.com/product/a", "http://lifestore.lk/product/a"]:
                self.assertFalse(live.check_live_availability(url)["availability_verified"])
            get.assert_not_called()

    def test_unexpected_pages_and_redirect(self):
        for response in [httpx.Response(200, text="Access denied", headers={"content-type": "text/html"}, request=httpx.Request("GET", URL)),
                         httpx.Response(302, headers={"location": "/products"}, request=httpx.Request("GET", URL))]:
            with patch.object(live.httpx, "get", return_value=response):
                self.assertFalse(live.check_live_availability(URL)["availability_verified"])

    def test_stock_parser_and_timeout(self):
        for quantity, expected in [(True, "in_stock"), (False, "out_of_stock")]:
            with patch.object(live.httpx, "get", return_value=page(quantity)) as get:
                self.assertEqual(live.check_live_availability(URL)["stock_status"], expected)
                self.assertEqual(get.call_args.kwargs["timeout"].read, 5)
                self.assertFalse(get.call_args.kwargs["follow_redirects"])

    def test_tool_exception_and_legacy_stock_safe(self):
        plan = {"answer_mode": "availability", "product_query": PRODUCT["name"]}
        for tool in [Mock(side_effect=RuntimeError("offline")), Mock(return_value={"products": [PRODUCT]})]:
            module = SimpleNamespace(lifestore_availability_lookup=tool)
            _, products, _ = chat._retrieve_products(module, "available?", plan)
            self.assertFalse(products[0]["availability_verified"])
            self.assertIn("couldn't verify", chat._write_lifestore_answer("available?", plan, products, "in stock"))

    def test_no_llm_availability_detection(self):
        for message in ["Is this router in stock?", "Do you have this product?", "Can I buy this now?", "Is it out of stock?", "Are these available?"]:
            self.assertEqual(chat._fallback_plan(message, 5)["answer_mode"], "availability")
        for message in ["Show me routers", "Show me cheapest router", "Tell me about this router", "Show me cheaper ones"]:
            self.assertNotEqual(chat._fallback_plan(message, 5)["answer_mode"], "availability")

    def test_multiple_ordinals_only_check_selected_pages(self):
        shown = [{**PRODUCT, "url": URL + str(i), "name": "Router " + str(i)} for i in range(3)]
        plan = {"answer_mode": "availability"}
        chat._resolve_availability_context(plan, {"state": {"last_products_shown": shown}},
                                           "Are the first and third ones available?")
        with patch.object(live.httpx, "get", return_value=page()) as get:
            _, products, _ = chat._retrieve_products(None, "available?", plan)
        self.assertEqual([p["url"] for p in products], [URL + "0", URL + "2"])
        self.assertEqual(get.call_count, 2)

    def test_incomplete_product_markup_is_not_out_of_stock(self):
        response = page()
        response = httpx.Response(200, request=response.request, headers=response.headers,
                                  text="<div>Home</div><div>Products</div><h1>Router</h1><p>Rs. 5000</p>")
        with patch.object(live.httpx, "get", return_value=response):
            self.assertFalse(live.check_live_availability(URL)["availability_verified"])


if __name__ == "__main__":
    unittest.main()
