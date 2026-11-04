"""Offline tests for category recognition and LifeStore planner normalization."""
import ast
import __future__
import json
from pathlib import Path
import re
import unittest
from typing import Any

from services import lifestore_prices
from services.lifestore_query_intent import (
    broad_category_query, category_price_bound, category_price_intent,
    remembered_ordinal_query, implicit_price_plan,
)


BACKEND = Path(__file__).resolve().parent


def planner_functions():
    source = BACKEND / "routers" / "lifestore_mcp_chat.py"
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    names = {"_fallback_plan", "_normalize_plan", "_plan_lifestore_answer"}
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    namespace = {
        "Any": Any,
        "json": json,
        "re": re,
        "broad_category_query": broad_category_query,
        "category_price_bound": category_price_bound,
        "category_price_intent": category_price_intent,
        "remembered_ordinal_query": remembered_ordinal_query,
        "implicit_price_plan": implicit_price_plan,
        "PRICE_INTENTS": lifestore_prices.PRICE_INTENTS,
        "parse_price": lifestore_prices.parse_price,
        "_safe_text": lambda value: "" if value is None else str(value).strip(),
        "_as_bool": lambda value, default=False: value if isinstance(value, bool) else default,
        "_bounded_int": lambda value, default, minimum, maximum: max(
            min(int(value), maximum), minimum) if str(value).lstrip("-").isdigit() else default,
    }
    module = ast.Module(body=functions, type_ignores=[])
    exec(compile(module, str(source), "exec", flags=__future__.annotations.compiler_flag), namespace)
    return namespace


class BroadCategoryQueryTests(unittest.TestCase):
    def test_category_requests(self):
        for message, expected in (
            ("tell me about routers", "routers"),
            ("ell mw about routers", "routers"),
            ("Tell me about the routers you have", "routers"),
            ("show me routers under 9000", "routers"),
            ("give routers under 15000", "routers"),
            ("show mw routers expensive than 15000", "routers"),
            ("list me wireless routers", "wireless routers"),
            ("browse 4g routers", "4g routers"),
        ):
            with self.subTest(message=message):
                self.assertEqual(broad_category_query(message), expected)

    def test_ranked_and_bounded_category_intents(self):
        self.assertEqual(category_price_intent("tell me about cheapest router"),
                         ("router", "cheapest"))
        self.assertEqual(category_price_intent("what is the expensive router"),
                         ("router", "most_expensive"))
        self.assertEqual(category_price_bound("tell me about routers under 9000"),
                         ("routers", "under", 9000.0))
        self.assertEqual(category_price_bound("show mw routers expensive than 15000"),
                 ("routers", "over", 15000.0))

    def test_named_products_and_non_browse_requests_are_not_categories(self):
        for message in (
            "tell me about TP-Link Archer C6 AC1200 Router",
            "is the router available",
            "compare routers and phones",
            "tell me about routers for TP-Link",
        ):
            with self.subTest(message=message):
                self.assertIsNone(broad_category_query(message))

    def test_ordinal_followup_uses_second_displayed_product_identity(self):
        memory = {"state": {"last_products_shown": [
            {"name": "Router One", "url": "https://lifestore.lk/product/router-one"},
            {"name": "Router Two", "url": "https://lifestore.lk/product/router-two"},
        ]}}
        self.assertEqual(remembered_ordinal_query("tell me about second one", memory), "Router Two")
        self.assertIsNone(remembered_ordinal_query("tell me about fifth one", memory))
        self.assertIsNone(remembered_ordinal_query("tell me about TP-Link Archer C6", memory))

    def test_wrong_single_product_plan_is_normalized_to_category_browse(self):
        planner = planner_functions()
        message = "tell me about routers"
        plan = planner["_normalize_plan"](
            {"answer_mode": "single_product", "product_query": message}, message, 5)
        self.assertEqual(plan["answer_mode"], "category_browse")
        self.assertEqual(plan["product_query"], "routers")
        self.assertEqual(plan["desired_product_count"], 5)

    def test_wrong_single_product_plan_is_normalized_to_cheapest_search(self):
        planner = planner_functions()
        message = "tell me about cheapest router"
        plan = planner["_normalize_plan"](
            {"answer_mode": "single_product", "product_query": message}, message, 5)
        self.assertEqual(plan["answer_mode"], "category_browse")
        self.assertEqual(plan["product_query"], "router")
        self.assertEqual(plan["price_intent"], "cheapest")
        self.assertEqual(plan["desired_product_count"], 1)

    def test_wrong_mode_keeps_literal_price_bound_when_browsing(self):
        planner = planner_functions()
        message = "show me routers under 9000"
        plan = planner["_normalize_plan"]({
            "answer_mode": "single_product", "product_query": message,
            "price_intent": "under", "max_price": 9000,
        }, message, 5)
        self.assertEqual(plan["answer_mode"], "category_browse")
        self.assertEqual(plan["product_query"], "routers")
        self.assertEqual(plan["price_intent"], "under")
        self.assertEqual(plan["max_price"], 9000)

    def test_expensive_than_query_keeps_category_and_lower_bound(self):
        planner = planner_functions()
        message = "show mw routers expensive than 15000"
        plan = planner["_normalize_plan"]({
            "answer_mode": "single_product", "product_query": message,
        }, message, 5)
        self.assertEqual(plan["answer_mode"], "category_browse")
        self.assertEqual(plan["product_query"], "routers")
        self.assertEqual(plan["price_intent"], "over")
        self.assertEqual(plan["min_price"], 15000)

    def test_fallback_browses_generic_category_but_preserves_named_model_lookup(self):
        planner = planner_functions()
        broad = planner["_fallback_plan"]("tell me about routers", 5)
        self.assertEqual(broad["answer_mode"], "category_browse")
        self.assertEqual(broad["product_query"], "routers")
        named_message = "tell me about TP-Link Archer C6 AC1200 Router"
        named = planner["_fallback_plan"](named_message, 5)
        self.assertEqual(named["answer_mode"], "single_product")
        self.assertEqual(named["product_query"], named_message)

    def test_fallback_preserves_ranked_and_bounded_category_search(self):
        planner = planner_functions()
        ranked = planner["_fallback_plan"]("tell me about cheapest router", 5)
        self.assertEqual((ranked["answer_mode"], ranked["product_query"], ranked["price_intent"]),
                         ("category_browse", "router", "cheapest"))
        bounded = planner["_fallback_plan"]("show me routers under 9000", 5)
        self.assertEqual((bounded["answer_mode"], bounded["product_query"],
                          bounded["price_intent"], bounded["max_price"]),
                         ("category_browse", "routers", "under", 9000.0))

    def test_ordinal_followup_is_resolved_before_llm_planner(self):
        planner = planner_functions()
        def unexpected_llm_call():
            raise AssertionError("ordinal memory lookup should not call the LLM")
        planner["_get_lifestore_openai_llm"] = unexpected_llm_call
        memory = {"state": {"last_products_shown": [
            {"name": "Router One", "url": "https://lifestore.lk/product/router-one"},
            {"name": "Router Two", "url": "https://lifestore.lk/product/router-two"},
        ]}}
        plan = planner["_plan_lifestore_answer"]("tell me about second one", 5, memory)
        self.assertEqual(plan["answer_mode"], "single_product")
        self.assertEqual(plan["product_query"], "Router Two")


if __name__ == "__main__":
    unittest.main()