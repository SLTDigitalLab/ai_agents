"""Run with python -m unittest discover -s backend -p test_lifestore_prices.py.

LLM and database boundaries are mocked; the actual router and MCP functions run.
"""
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from routers import lifestore_mcp_chat as chat
from services import lifestore_memory as memory
from lifestore_memory_test_support import install_memory_database
from services.lifestore_prices import parse_price, product_price


def product(name, price, kind="Router"):
    return {"product_id": name, "name": name, "price_value": price, "product_type": kind}


class PriceTests(unittest.TestCase):
    def setUp(self):
        install_memory_database(self)
        self.products = [product("B", 15000), product("unknown", None),
                         product("A", 5000), product("C", 10000), product("D", 20000)]

    def plan(self, intent, **fields):
        return chat._normalize_plan({"price_intent": intent, "product_query": "router",
                                    "answer_mode": "category_browse", **fields}, "query", 5)

    def test_parse_and_precedence(self):
        for value in (3800, 3800.0, "3800", "3,800", "Rs. 3,800", "LKR 3,800", "Rs 3,800.00"):
            with self.subTest(value=value):
                self.assertEqual(parse_price(value), 3800)
        for value in (None, "", "unknown", True, -1, float("nan"), float("inf"), {}, "3,80", "10-20", "Rs. 5 monthly"):
            with self.subTest(value=value):
                self.assertIsNone(parse_price(value))
        self.assertEqual(product_price({"price_value": 0, "price": 100}), 0)
        self.assertEqual(product_price({"price_value": "bad", "price": "Rs. 3,800"}), 3800)

    def test_normalize(self):
        p = self.plan("between", min_price="Rs. 15,000", max_price="5000", reference_price="bad")
        self.assertEqual((p["min_price"], p["max_price"], p["reference_price"]), (5000, 15000, None))
        self.assertEqual(self.plan("unsupported")["price_intent"], "none")

    def test_all_constraints(self):
        cases = [("cheapest", {}, [5000, 10000, 15000, 20000]),
                 ("most_expensive", {}, [20000, 15000, 10000, 5000]),
                 ("under", {"max_price": 10000}, [5000]),
                 ("over", {"min_price": 15000}, [20000]),
                 ("between", {"min_price": 5000, "max_price": 15000}, [15000, 5000, 10000]),
                 ("cheaper_than", {"reference_price": 15000}, [5000, 10000]),
                 ("more_expensive_than", {"reference_price": 10000}, [15000, 20000])]
        for intent, fields, expected in cases:
            with self.subTest(intent=intent):
                result = chat._apply_price_constraints(self.products, self.plan(intent, **fields))
                self.assertEqual([product_price(p) for p in result], expected)
                self.assertTrue(all(any(p is original for original in self.products) for p in result))

    def test_empty_equal_missing_and_single(self):
        self.assertEqual(chat._apply_price_constraints([], self.plan("cheapest")), [])
        self.assertEqual(chat._apply_price_constraints(self.products, self.plan("under")), [])
        self.assertEqual(chat._apply_price_constraints(self.products, self.plan("under", max_price=1)), [])
        equal = [product("one", 1), product("two", 1)]
        self.assertEqual(chat._apply_price_constraints(equal, self.plan("cheapest")), equal)
        self.assertEqual(chat._apply_price_constraints(equal[:1], self.plan("cheapest")), equal[:1])

    def test_broad_retrieval_before_slice(self):
        candidates = [product(str(i), 10000 + i) for i in range(30)] + [product("winner", 1)]
        module = SimpleNamespace(lifestore_hybrid_product_search=Mock(return_value={"products": candidates, "answer": "wrong answer"}))
        result, products, _ = chat._retrieve_products(module, "cheapest router", self.plan("cheapest", desired_product_count=1))
        self.assertEqual(products[0]["name"], "winner")
        self.assertEqual(result["answer"], "")
        self.assertEqual(result["retrieval"]["returned_products"], 1)
        self.assertEqual(module.lifestore_hybrid_product_search.call_args.kwargs["limit"], 100)

    def test_relative_context_and_numbered_list(self):
        memory.clear_conversation("test")
        memory.save_products_shown("test", self.products)
        memory.set_current_category("test", "Router")
        memory.save_products_shown("test", [self.products[0]])
        state = memory.get_conversation_memory("test")
        self.assertEqual(len(state["state"]["last_products_shown"]), 5)
        self.assertEqual(state["state"]["last_selected_product"]["name"], "B")
        p = self.plan("cheaper_than", reference_price=999999)
        chat._resolve_price_context(p, state)
        self.assertEqual(p["reference_price"], 15000)
        self.assertEqual(p["product_query"], "Router")
        speakers = [product("s1", 7000, "Speaker"), product("s2", 9000, "Speaker")]
        memory.save_products_shown("test", speakers)
        p = self.plan("cheaper_than")
        chat._resolve_price_context(p, memory.get_conversation_memory("test"))
        self.assertEqual((p["product_query"], p["reference_price"]), ("Speaker", 7000))

    def test_missing_context_clarifies_without_retrieval(self):
        p = self.plan("cheaper_than", reference_price=10000)
        chat._resolve_price_context(p, {})
        result, products, tool = chat._retrieve_products(None, "cheaper ones", p)
        self.assertEqual(products, [])
        self.assertIsNone(tool)
        self.assertIn("Which product", chat._write_lifestore_answer("", p, [], ""))
        p = self.plan("under")
        chat._resolve_price_context(p, {})
        self.assertIn("price_clarification", p)

    def test_filtered_empty_never_uses_unfiltered_answer(self):
        answer = chat._write_lifestore_answer("", self.plan("cheapest"), [], "Buy Router B")
        self.assertNotIn("Router B", answer)

    def test_planner_schema_and_conversation_flow(self):
        memory.clear_conversation("flow")
        routers = [product("Router A", 5000), product("Router B", 15000), product("Router C", 20000)]
        module = SimpleNamespace(
            lifestore_hybrid_product_search=Mock(return_value={"products": routers}),
            lifestore_precise_product_lookup=Mock(side_effect=lambda product_query, **kw: {
                "products": [p for p in routers if p["name"] == product_query]}),
        )
        plans = [("Show me routers", "category_browse", "router"),
                 ("Tell me about the second one", "single_product", "Router B"),
                 ("How much is it?", "single_product", "Router B"),
                 ("What about the first one?", "single_product", "Router A")]
        llm = Mock()
        def writer(message, plan, products, fallback):
            return ", ".join(p["name"] for p in products)
        with patch.object(chat, "_load_mcp_module", return_value=module), patch.object(chat, "_get_lifestore_openai_llm", return_value=llm), patch.object(chat, "_write_lifestore_answer", side_effect=writer):
            for message, mode, query in plans:
                llm.invoke.return_value = SimpleNamespace(content=json.dumps({"answer_mode": mode, "product_query": query}))
                response = chat.lifestore_mcp_chat(chat.LifeStoreMCPChatRequest(message=message, thread_id="flow"))
                self.assertEqual(response["status"], "success")
                if mode == "single_product":
                    self.assertEqual(response["products"][0]["name"], query)
            prompt = str(llm.invoke.call_args)
            self.assertIn("cheaper_than", prompt)
            self.assertIn("Router B", prompt)
            self.assertIn("Router C", prompt)
        state = memory.get_conversation_memory("flow")["state"]
        self.assertEqual(len(state["last_products_shown"]), 3)
        self.assertEqual(state["last_selected_product"]["name"], "Router A")

    def test_existing_routes_purchase_comparison_availability(self):
        module = SimpleNamespace(**{name: Mock(return_value={"products": self.products}) for name in
            ("lifestore_precise_product_lookup", "lifestore_availability_lookup", "lifestore_compare_products")})
        for mode, tool in [("purchase", "lifestore_precise_product_lookup"),
                           ("availability", "lifestore_availability_lookup"),
                           ("comparison", "lifestore_compare_products")]:
            p = chat._normalize_plan({"answer_mode": mode}, "Router B", 5)
            _, products, used = chat._retrieve_products(module, "Router B", p)
            self.assertEqual(used, tool)
            self.assertEqual(len(products), p["desired_product_count"])

    def test_greeting_and_purchase_response_contract(self):
        module = SimpleNamespace(lifestore_precise_product_lookup=Mock(return_value={"products": [self.products[0]]}))
        with patch.object(chat, "_load_mcp_module", return_value=module), patch.object(chat, "_get_lifestore_openai_llm", return_value=None):
            for mode in ("greeting", "purchase"):
                plan = chat._normalize_plan({"answer_mode": mode, "open_lifestore_form": mode == "purchase"}, "hello", 5)
                with patch.object(chat, "_plan_lifestore_answer", return_value=plan):
                    response = chat.lifestore_mcp_chat(chat.LifeStoreMCPChatRequest(message="hello"))
                self.assertEqual(response["status"], "success")
                self.assertIn("frontend_contract", response)
                if mode == "greeting":
                    self.assertEqual(response["products"], [])
                    self.assertIsNone(response["form_payload"])
                else:
                    self.assertIn("[RENDER_LIFESTORE_FORM]", response["answer"])
                    self.assertEqual(response["form_payload"]["product"], "B")

    def test_missing_price_category_clarifies(self):
        plan = chat._normalize_plan({"price_intent": "cheapest"}, "cheapest please", 1)
        chat._resolve_price_context(plan, {})
        self.assertIn("price_clarification", plan)

    def test_narrow_context_survives_single_followup(self):
        speakers = [{"name": "Speaker A", "price_value": 7000, "category": "Audio"},
                    {"name": "Speaker B", "price_value": 9000, "category": "Audio"}]
        memory.clear_conversation("narrow")
        memory.save_products_shown("narrow", speakers)
        memory.set_current_category("narrow", "speaker")
        module = SimpleNamespace(lifestore_precise_product_lookup=Mock(return_value={"products": [speakers[1]]}))
        plan = chat._normalize_plan({"answer_mode": "single_product", "product_query": "Speaker B"}, "second one", 1)
        with patch.object(chat, "_load_mcp_module", return_value=module), patch.object(chat, "_plan_lifestore_answer", return_value=plan), patch.object(chat, "_get_lifestore_openai_llm", return_value=None):
            chat.lifestore_mcp_chat(chat.LifeStoreMCPChatRequest(message="second one", thread_id="narrow"))
        relative = self.plan("cheaper_than")
        chat._resolve_price_context(relative, memory.get_conversation_memory("narrow"))
        self.assertEqual((relative["product_query"], relative["reference_price"]), ("speaker", 9000))


class MCPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = chat._load_mcp_module()

    def test_candidate_limit_and_normal_limit(self):
        s = self.server
        rows = [product(str(i), i + 1) for i in range(40)]
        def graph(query, limit):
            return rows[:limit]
        def merge(graph_rows, **kwargs):
            return graph_rows
        with patch.object(s, "_graph_product_search_rows", side_effect=graph), patch.object(s, "_merge_graph_and_vector_results", side_effect=merge), patch.object(s, "_qdrant_search_documents", return_value=[]):
            self.assertEqual(len(s.lifestore_hybrid_product_search("router", limit=100)["products"]), 8)
            self.assertEqual(len(s.lifestore_hybrid_product_search("router", limit=100, price_candidates=True)["products"]), 40)

    def test_local_catalog_family_and_formatted_price(self):
        s = self.server
        catalog = [dict(product("Router A", None), price="Rs. 8,500.00"),
                   product("Router B", 12000), product("Speaker C", 1, "Speaker")]
        with patch.object(s, "load_products", return_value=catalog):
            rows = s._local_product_search_rows("router", 100)
        self.assertEqual({p["name"] for p in rows}, {"Router A", "Router B"})
        result = chat._apply_price_constraints(rows, {"price_intent": "cheapest"})
        self.assertEqual(product_price(result[0]), 8500)

    def test_price_search_keeps_images_deferred_without_page_fetch(self):
        s = self.server
        rows = [dict(product("Router A", 100), url="https://lifestore.lk/products/a"),
                dict(product("Router B", 200), url="https://lifestore.lk/products/b")]
        def resolve(product_url, current_image_url="", allow_page_fetch=True):
            return {"image_url": "https://lifestore.lk/a.jpg" if allow_page_fetch else "",
                    "image_source": "graph" if allow_page_fetch else "deferred"}
        with patch.object(s, "_graph_product_search_rows", return_value=rows), patch.object(s, "_resolve_product_image", side_effect=resolve) as images:
            plan = chat._normalize_plan({"price_intent": "cheapest", "product_query": "router", "desired_product_count": 1}, "cheapest router", 1)
            _, products, _ = chat._retrieve_products(s, "cheapest router", plan)
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0]["image_url"], "")
        self.assertEqual(len(images.call_args_list), 3)
        self.assertFalse(images.call_args_list[0].kwargs["allow_page_fetch"])
        self.assertTrue(all(call.kwargs["allow_page_fetch"] is False for call in images.call_args_list))

    def test_checked_in_catalog_price_search(self):
        s = self.server
        path = Path(__file__).resolve().parent / "data" / "real" / "lifestore_all.json"
        with patch.object(s, "PRODUCTS_JSON_PATH", path), patch.object(s, "neo4j_driver", return_value=None):
            rows = s._local_product_search_rows("router", 100)
        self.assertTrue(rows)
        matches = chat._apply_price_constraints(rows, {"price_intent": "under", "max_price": 10000})
        self.assertTrue(matches)
        self.assertTrue(all(product_price(p) < 10000 for p in matches))


if __name__ == "__main__":
    unittest.main()
