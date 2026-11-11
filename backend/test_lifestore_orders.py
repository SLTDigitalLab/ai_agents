"""CB-20: explicit order status fails closed without product retrieval."""
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from routers import lifestore_mcp_chat as chat
from services import lifestore_memory as memory
from lifestore_memory_test_support import install_memory_database


class OrderStatusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = chat._load_mcp_module()

    def setUp(self):
        install_memory_database(self)
        memory.clear_conversation("cb20")
        self.addCleanup(memory.clear_conversation, "cb20")

    def ask(self, message):
        return chat.lifestore_mcp_chat(chat.LifeStoreMCPChatRequest(message=message, thread_id="cb20"))

    def test_status_bypasses_product_and_misleading_planner(self):
        with ExitStack() as stack:
            forbidden = [stack.enter_context(patch.object(self.server, name)) for name in (
                "lifestore_hybrid_product_search", "lifestore_precise_product_lookup",
                "_qdrant_search_documents", "_graph_product_search_rows", "_graph_identity_rows",
                "find_product_by_id_or_name", "_local_product_search_rows")]
            forbidden += [stack.enter_context(patch.object(chat, name)) for name in (
                "_retrieve_products", "_resolve_price_context", "_apply_price_constraints",
                "_get_lifestore_openai_llm", "_write_lifestore_answer")]
            planner = stack.enter_context(patch.object(chat, "_plan_lifestore_answer", return_value={
                "answer_mode": "single_product", "product_query": "22222222222222"}))
            for reference in ["#qwerty11111111", "#22222222222222"]:
                for phrase in ["check status of order", "track my order", "where is my order",
                               "order status", "status of my order", "check my order", "check order", "track order"]:
                    with self.subTest(reference=reference, phrase=phrase):
                        result = self.ask(f"{phrase} {reference}")
                        self.assertEqual(result["status"], "unavailable")
                        self.assertEqual(result["order_reference"], reference)
                        self.assertIsNone(result["order"])
                        self.assertEqual(result["products"], [])
                        self.assertIsNone(result["tool_name"])
                        self.assertIsNone(result["form_payload"])
                        self.assertEqual(result["answer_plan"]["answer_mode"], "order_status")
                        self.assertIn("can't verify that order reference", result["answer"])
            for mock in forbidden + [planner]:
                mock.assert_not_called()

    def test_status_does_not_require_working_mcp(self):
        with patch.object(chat, "_load_mcp_module", side_effect=RuntimeError("MCP unavailable")) as load:
            self.assertEqual(self.ask("track order #22222222222222")["status"], "unavailable")
            load.assert_not_called()

    def test_reference_format_and_missing_reference(self):
        for message, reference in [("  TRACK MY ORDER #000222?  ", "#000222"),
                                   ("check order ABC123", "ABC123"),
                                   ("order status: 000222", "000222"),
                                   ("track my order", None), ("order status", None)]:
            with self.subTest(message=message):
                result = self.ask(message)
                self.assertEqual(result["order_reference"], reference)
                self.assertEqual(result["status"], "unavailable")

    def test_shopping_memory_preserved_and_exchange_recorded(self):
        memory.set_current_category("cb20", "Router")
        memory.set_pending_action("cb20", "purchase")
        before = memory.get_conversation_memory("cb20")["state"]
        result = self.ask("check status of order #22222222222222")
        after = memory.get_conversation_memory("cb20")
        self.assertEqual(after["state"], before)
        self.assertEqual(after["messages"][-2]["role"], "user")
        self.assertEqual(after["messages"][-1]["content"], result["answer"])

    def test_purchase_and_products_keep_existing_paths(self):
        product = {"name": "TP-Link Archer C6", "product_id": "C6",
                   "url": "https://lifestore.lk/product/tp-link-archer-c6", "price": 10000}
        for message, mode in [("I want to order TP-Link Archer C6", "purchase"),
                              ("Can I order this router?", "purchase"),
                              ("show me product 22222222222222", "single_product"),
                              ("show me routers", "category_browse")]:
            with self.subTest(message=message):
                self.assertIsNone(chat._order_status_response(message))
                with ExitStack() as stack:
                    planner = stack.enter_context(patch.object(chat, "_plan_lifestore_answer", return_value={
                        "answer_mode": mode, "product_query": "TP-Link Archer C6", "open_lifestore_form": mode == "purchase"}))
                    stack.enter_context(patch.object(chat, "_get_lifestore_openai_llm", return_value=None))
                    hybrid = stack.enter_context(patch.object(self.server, "lifestore_hybrid_product_search", return_value={"products": [product]}))
                    precise = stack.enter_context(patch.object(self.server, "lifestore_precise_product_lookup", return_value={"products": [product]}))
                    result = self.ask(message)
                    planner.assert_called_once()
                    self.assertEqual(result["answer_plan"]["answer_mode"], mode)
                    self.assertEqual(result["products"][0]["name"], product["name"])
                    (hybrid if mode == "category_browse" else precise).assert_called_once()
                    if mode == "purchase":
                        self.assertIsNotNone(result["form_payload"])


if __name__ == "__main__":
    unittest.main()
