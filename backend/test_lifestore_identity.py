"""Identity regression tests use misleading candidates and planner rewrites."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from routers import lifestore_mcp_chat as chat
from services.lifestore_identity import CLARIFICATION, select_identity_product, dedupe_identity_candidates


def product(name, **fields):
    return {"name": name, "url": "https://lifestore.lk/product/" + name.lower().replace(" ", "-"),
            "stock": 1, "stock_status": "in_stock", **fields}


PHONE = product("COMSTOX SI001 CLI Telephone", brand="COMSTOX", model="SI001")
WRONG_PHONE = product("Prolink HCD52C-CLI Telephone", brand="Prolink", model="HCD52C")
UPS = product("PROLINK PRO2000SFCU LINE INTERACTIVE UPS", brand="Prolink", model="PRO2000SFCU")
WRONG_UPS = product("Prolink Powerline Adaptor Kit", brand="Prolink")
LIVE = {"stock": 0, "stock_status": "out_of_stock", "availability_verified": True, "source": "live_website"}


class IdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = chat._load_mcp_module()

    def test_explicit_availability_ignores_wrong_planner_and_candidate_order(self):
        for query, correct, wrong in [
            ("Is COMSTOX SI001 CLI Telephone currently available?", PHONE, WRONG_PHONE),
            ("how about COMSTOX SI001 CLI Telephone. is it currently available", PHONE, WRONG_PHONE),
            ("How about PROLINK PRO2000SFCU LINE INTERACTIVE UPS?", UPS, WRONG_UPS),
        ]:
            with self.subTest(query=query), \
                 patch.object(self.server, "_graph_identity_rows", return_value=[wrong, correct]), \
                 patch.object(self.server, "load_products", return_value=[]), \
                 patch.object(self.server, "check_live_availability", return_value=LIVE) as live:
                plan = {"answer_mode": "availability", "product_query": wrong["name"],
                        "availability_products": [wrong]}
                _, products, _ = chat._retrieve_products(self.server, query, plan)
                self.assertEqual(products[0]["name"], correct["name"])
                self.assertEqual(products[0]["stock_status"], "out_of_stock")
                live.assert_called_once_with(correct["url"])

    def test_how_about_ups_without_availability_intent_does_not_scrape(self):
        with patch.object(self.server, "_graph_identity_rows", return_value=[WRONG_UPS, UPS]), \
             patch.object(self.server, "load_products", return_value=[]), \
             patch.object(self.server, "_qdrant_search_documents", return_value=[]), \
             patch.object(self.server, "check_live_availability") as live:
            _, products, _ = chat._retrieve_products(self.server,
                "How about PROLINK PRO2000SFCU LINE INTERACTIVE UPS?",
                {"answer_mode": "single_product", "product_query": WRONG_UPS["name"]})
            self.assertEqual(products[0]["name"], UPS["name"])
            live.assert_not_called()

    def test_exact_local_identity_not_hidden_by_graph_similar_hit(self):
        with patch.object(self.server, "_graph_identity_rows", return_value=[WRONG_PHONE]), \
             patch.object(self.server, "load_products", return_value=[PHONE]):
            result = self.server.lifestore_precise_product_lookup(PHONE["name"], include_vector_evidence=False)
        self.assertEqual(result["products"][0]["name"], PHONE["name"])

    def test_unknown_model_and_ambiguous_query_never_scrape(self):
        for query in ["COMSTOX SI999 CLI Telephone", "PROLINK PRO9999 UPS", "CLI Telephone", "Prolink"]:
            with self.subTest(query=query), \
                 patch.object(self.server, "_graph_identity_rows", return_value=[WRONG_PHONE, PHONE, UPS, WRONG_UPS]), \
                 patch.object(self.server, "load_products", return_value=[]), \
                 patch.object(self.server, "check_live_availability") as live:
                result = self.server.lifestore_availability_lookup(query)
                self.assertEqual(result["products"], [])
                self.assertEqual(result["message"], CLARIFICATION)
                live.assert_not_called()

    def test_normalized_names_ids_skus_models_and_strong_tokens(self):
        catalog = [WRONG_PHONE, WRONG_UPS, {**PHONE, "product_id": "LS001", "sku": "TEL001"}, UPS]
        for query in ["comstox si001 cli telephone", "COMSTOX SI001-CLI Telephone!", "LS001", "TEL001", "SI001", "COMSTOX SI001"]:
            with self.subTest(query=query):
                self.assertEqual(select_identity_product(query, catalog)["name"], PHONE["name"])
        self.assertIsNone(select_identity_product("PROLINK SI001", catalog))
        self.assertIsNone(select_identity_product("COMSTOX HCD52C", catalog))

    def test_ties_and_description_only_model_do_not_establish_identity(self):
        variant = product("COMSTOX SI001 Wireless CLI Telephone", model="SI001")
        self.assertIsNone(select_identity_product("COMSTOX SI001", [PHONE, variant]))
        decoy = {**WRONG_PHONE, "description": PHONE["name"], "graph_score": 999999}
        self.assertIsNone(select_identity_product(PHONE["name"], [decoy]))
        self.assertEqual(select_identity_product(PHONE["name"], [variant, PHONE]), PHONE)

    def test_lookup_failure_does_not_fall_back_to_semantic_substitution(self):
        module = SimpleNamespace(lifestore_precise_product_lookup=Mock(side_effect=RuntimeError("offline")),
                                 lifestore_hybrid_product_search=Mock(return_value={"products": [WRONG_UPS]}))
        result, products, _ = chat._retrieve_products(module, UPS["name"], {"answer_mode": "single_product"})
        self.assertEqual(products, [])
        self.assertEqual(chat._write_lifestore_answer(UPS["name"], {}, products, result["answer"]), CLARIFICATION)
        module.lifestore_hybrid_product_search.assert_not_called()

    def test_graph_identity_candidates_do_not_use_semantic_limit_or_stock_filter(self):
        session = Mock()
        session.run.return_value = [{"product": WRONG_PHONE}, {"product": PHONE}]
        driver = Mock()
        driver.session.return_value.__enter__ = Mock(return_value=session)
        driver.session.return_value.__exit__ = Mock(return_value=False)
        with patch.object(self.server, "neo4j_driver", return_value=driver):
            rows = self.server._graph_identity_rows("Is COMSTOX SI001 CLI Telephone in stock?")
        self.assertEqual(rows, [WRONG_PHONE, PHONE])
        cypher = session.run.call_args.args[0]
        self.assertNotIn("LIMIT", cypher)
        self.assertNotIn("stock_status", cypher)
        self.assertNotIn("description", cypher)
        self.assertIn("si001", session.run.call_args.kwargs["terms"])
        driver.close.assert_called_once()

    def test_qdrant_only_identities_from_bounded_pool(self):
        names = [UPS["name"], "Prolink Powerline Adaptor Kit",
                 "TP-Link Powerline Adapter WPA4220 KIT", "Tenda MX3 2-Pack Mesh Wi-Fi 6 System"]
        for name in names:
            expected = product(name)
            docs = [{"title": WRONG_PHONE["name"], "link": WRONG_PHONE["url"], "rank": 1},
                    {"metadata": {"Header 1": name, "link": expected["url"]}, "rank": 30}]
            with self.subTest(name=name), \
                 patch.object(self.server, "_graph_identity_rows", return_value=[]), \
                 patch.object(self.server, "load_products", return_value=[]), \
                 patch.object(self.server, "_graph_product_search_rows", return_value=[]) as graph, \
                 patch.object(self.server, "_qdrant_search_documents", return_value=docs) as vector, \
                 patch.object(self.server, "check_live_availability", return_value=LIVE) as live:
                result = self.server.lifestore_availability_lookup(name)
                self.assertEqual(result["products"][0]["name"], name)
                live.assert_called_once_with(expected["url"])
                self.assertEqual(graph.call_args.kwargs["limit"], 32)
                self.assertEqual(vector.call_args.kwargs["limit"], 32)

    def test_broader_graph_search_is_candidate_source(self):
        with patch.object(self.server, "_graph_identity_rows", return_value=[]), \
             patch.object(self.server, "load_products", return_value=[]), \
             patch.object(self.server, "_graph_product_search_rows", return_value=[WRONG_UPS, UPS]), \
             patch.object(self.server, "_qdrant_search_documents", return_value=[]):
            result = self.server.lifestore_precise_product_lookup(UPS["name"], False)
        self.assertEqual(result["products"][0]["name"], UPS["name"])

    def test_vector_similarity_and_body_are_not_identity_proof(self):
        docs = [{"title": WRONG_PHONE["name"], "link": WRONG_PHONE["url"],
                 "text": PHONE["name"], "score": 1.0},
                {"link": PHONE["url"], "text": PHONE["name"]}]
        with patch.object(self.server, "_graph_identity_rows", return_value=[]), \
             patch.object(self.server, "load_products", return_value=[]), \
             patch.object(self.server, "_graph_product_search_rows", return_value=[]), \
             patch.object(self.server, "_qdrant_search_documents", return_value=docs), \
             patch.object(self.server, "check_live_availability") as live:
            result = self.server.lifestore_availability_lookup(PHONE["name"])
        self.assertEqual(result["message"], CLARIFICATION)
        live.assert_not_called()

    def test_typo_tolerance_requires_exact_unique_brand_model(self):
        name = "Prolink DH5201 Dual-band Wi-Fi Extender"
        extender = product(name, brand="Prolink")
        for query in [name, name[:-1]]:
            self.assertEqual(select_identity_product(query, [extender]), extender)
        for query in [name.replace("DH5201", "DH5202"), name.replace("Prolink", "Prolinx"),
                      name.replace("Prolink", "COMSTOX"), "PROLINK SI001"]:
            self.assertIsNone(select_identity_product(query, [extender, PHONE]))
        variant = product(name + " Plus", brand="Prolink")
        self.assertIsNone(select_identity_product(name[:-1], [extender, variant]))
        # Metadata-free names may establish the exact brand prefix and model.
        self.assertEqual(select_identity_product(name[:-1], [product(name)]), product(name))

    def test_vector_ambiguity_and_duplicate_chunks(self):
        docs = [{"title": PHONE["name"], "link": PHONE["url"]}] * 3
        candidates = dedupe_identity_candidates(self.server._vector_identity_candidates(docs))
        self.assertEqual(len(candidates), 1)
        variant = product("COMSTOX SI001 Wireless CLI Telephone", brand="COMSTOX")
        docs.append({"title": variant["name"], "link": variant["url"]})
        candidates = dedupe_identity_candidates(self.server._vector_identity_candidates(docs))
        self.assertIsNone(select_identity_product("COMSTOX SI001", candidates))

    def test_candidate_deduplication_preserves_source_precedence_and_variants(self):
        self.assertEqual(dedupe_identity_candidates([PHONE, {**PHONE, "stock": 0}]), [PHONE])
        no_url = {**PHONE, "url": ""}
        self.assertEqual(len(dedupe_identity_candidates([PHONE, no_url])), 1)
        variant = {**PHONE, "name": PHONE["name"] + " Wireless", "url": ""}
        self.assertEqual(len(dedupe_identity_candidates([no_url, variant])), 2)

    def test_vector_titles_breadcrumbs_and_product_url_validation(self):
        docs = [{"title": UPS["name"] + " > Overview", "link": UPS["url"]},
                {"title": PHONE["name"], "link": "https://lifestore.lk/products"}]
        candidates = self.server._vector_identity_candidates(docs)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["name"], UPS["name"])


if __name__ == "__main__":
    unittest.main()
