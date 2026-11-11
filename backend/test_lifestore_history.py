"""Historical price recall through the router and persistent-memory boundary."""
import importlib
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from routers import lifestore_mcp_chat as chat
from services import lifestore_memory as memory
from services.lifestore_prices import historical_price_reference
from lifestore_memory_test_support import install_memory_database

QUESTION = 'what was the cheapest router we discussed earlier?'
SUMMARY = ('The user was shopping for routers. The cheapest router discussed was '
           'Prolink PRS1140 ADSL Router at Rs. 3,800.00.')
PRODUCTS = [
    {'product_id': 'A', 'name': 'Prolink PRS1140 ADSL Router', 'price_value': 3800, 'product_type': 'Router'},
    {'product_id': 'B', 'name': 'Router B', 'price_value': 8910, 'product_type': 'Router'},
    {'product_id': 'C', 'name': 'Router C', 'price_value': 14980, 'product_type': 'Router'},
]


class HistoricalPriceTests(unittest.TestCase):
    def setUp(self):
        self.db = install_memory_database(self)
        memory.save_products_shown('history', PRODUCTS)
        memory.set_last_selected_product('history', PRODUCTS[0])
        memory.set_current_category('history', 'router')
        for i in range(16):
            memory.save_message('history', 'user' if i % 2 == 0 else 'assistant', f'recent {i}')
        payload = memory.get_summarization_payload('history')
        memory.apply_conversation_summary('history', SUMMARY, payload['recent_messages'])

    def ask(self, message=QUESTION, thread='history'):
        return chat.lifestore_mcp_chat(chat.LifeStoreMCPChatRequest(message=message, thread_id=thread))

    def history_answer(self, message=QUESTION, thread='history'):
        with patch.object(chat, '_load_mcp_module') as loader, \
             patch.object(chat, '_plan_lifestore_answer') as planner, \
             patch.object(chat, '_maybe_summarize_conversation'):
            result = self.ask(message, thread)
        loader.assert_not_called()
        planner.assert_not_called()
        self.assertEqual(result['answer_plan']['answer_mode'], 'conversation_history')
        self.assertEqual(result['retrieval']['source'], 'conversation_memory')
        self.assertIsNone(result['tool_name'])
        self.assertIsNone(result['form_payload'])
        self.assertEqual(result['products'], [])
        return result

    def test_cheapest_uses_remembered_numeric_prices_without_lookup(self):
        before = memory.get_conversation_memory('history')['state']
        result = self.history_answer()
        self.assertIn('Prolink PRS1140 ADSL Router at Rs. 3,800.00', result['answer'])
        self.assertNotIn('confirm', result['answer'])
        self.assertEqual(memory.get_conversation_memory('history')['state'], before)
        self.assertEqual(memory.get_conversation_memory('history')['messages'][-2:], [
            {'role': 'user', 'content': QUESTION}, {'role': 'assistant', 'content': result['answer']}])

    def test_most_expensive_ignores_selected_cheapest_and_summary_ranking(self):
        result = self.history_answer('what was the most expensive router we discussed earlier?')
        self.assertIn('Router C at Rs. 14,980.00', result['answer'])

    def test_mixed_categories_only_compare_routers(self):
        memory.save_products_shown('history', [*PRODUCTS,
            {'name': 'Speaker low', 'product_type': 'Speaker', 'price_value': 1},
            {'name': 'Speaker high', 'product_type': 'Speaker', 'price_value': 99999}])
        for extreme, name in [('cheapest', PRODUCTS[0]['name']), ('most expensive', 'Router C')]:
            result = self.history_answer(f'{extreme} router we discussed')
            self.assertIn(name, result['answer'])
            self.assertNotIn('Speaker', result['answer'])

    def test_selected_product_outside_recent_subset_is_included(self):
        memory.save_products_shown('history', PRODUCTS[1:])
        memory.set_last_selected_product('history', PRODUCTS[0])
        result = self.history_answer()
        self.assertIn('Prolink PRS1140', result['answer'])
        self.assertIn('still have saved', result['answer'])

    def test_unknown_or_summary_only_memory_clarifies_without_catalog(self):
        for thread in ('missing', 'summary-only'):
            if thread == 'summary-only':
                memory.set_summary(thread, SUMMARY)
            result = self.history_answer(thread=thread)
            self.assertIn("don't have enough", result['answer'])
            self.assertNotIn('Prolink', result['answer'])
        result = self.history_answer('cheapest laptop we discussed')
        self.assertIn("don't have enough", result['answer'])

    def test_missing_prices_and_ties_are_explicit(self):
        memory.save_products_shown('history', [
            {'name': 'Router unknown'},
            {'name': 'Router X', 'price_value': 3800},
            {'name': 'Router Y', 'price_value': 3800}])
        result = self.history_answer()
        self.assertIn('Router X, Router Y', result['answer'])
        self.assertIn('no saved price', result['answer'])

    def test_service_reload_reads_same_persisted_shape_and_answer(self):
        expected = memory.get_conversation_memory('history')
        self.assertEqual(set(expected), {'summary', 'messages', 'state'})
        self.assertEqual(set(expected['state']), {'current_category', 'last_products_shown',
                                               'last_selected_product', 'pending_action'})
        self.assertEqual(expected['summary'], SUMMARY)
        self.assertEqual(len(expected['messages']), 6)
        first = self.history_answer()['answer']
        # Reload the actual service; only the mocked DB rows survive this reset.
        importlib.reload(memory)
        with patch.object(chat, '_MCP_MODULE', None):
            chat._get_lifestore_openai_llm.cache_clear()
            loaded = memory.get_conversation_memory('history')
            self.assertEqual(loaded['state'], expected['state'])
            self.assertEqual(loaded['summary'], SUMMARY)
            self.assertEqual(self.history_answer()['answer'], first)

    def test_planner_receives_all_restored_memory_fields(self):
        restored = memory.get_conversation_memory('history')
        llm = Mock()
        llm.invoke.return_value = SimpleNamespace(content=json.dumps({
            'answer_mode': 'single_product', 'product_query': PRODUCTS[0]['name']}))
        with patch.object(chat, '_get_lifestore_openai_llm', return_value=llm):
            chat._plan_lifestore_answer(QUESTION, 5, restored)
        prompt = llm.invoke.call_args.args[0][1][1]
        for text in (SUMMARY, 'Current category:\nrouter', 'Last products shown:',
                     'Last selected product:', 'Router B', 'Router C', '3800', 'recent 10', 'recent 15'):
            self.assertIn(text, prompt)
        self.assertNotIn('recent 9', prompt)

    def test_original_fallback_and_identity_path_reproduces_failure(self):
        # Exercise the pre-fix downstream path directly; the router now bypasses it.
        plan = chat._fallback_plan(QUESTION, 5)
        self.assertEqual(plan['answer_mode'], 'single_product')
        self.assertEqual(plan['price_intent'], 'none')
        self.assertEqual(chat.explicit_identity_query(QUESTION), 'cheapest router we discussed earlier')
        module = SimpleNamespace(lifestore_precise_product_lookup=Mock(
            return_value={'status': 'not_found', 'answer': chat.CLARIFICATION, 'products': []}))
        chat._resolve_price_context(plan, memory.get_conversation_memory('history'))
        result, products, tool = chat._retrieve_products(module, QUESTION, plan)
        self.assertEqual(tool, 'lifestore_precise_product_lookup')
        self.assertEqual(result['answer'], chat.CLARIFICATION)
        self.assertEqual(products, [])
        self.assertEqual(module.lifestore_precise_product_lookup.call_args.kwargs['product_query'],
                         'cheapest router we discussed earlier')

    def test_current_catalog_queries_keep_existing_retrieval(self):
        for message in ('what is the cheapest router?', 'show me cheapest routers'):
            self.assertIsNone(historical_price_reference(message, memory.get_conversation_memory('history')))
            llm = Mock()
            llm.invoke.return_value = SimpleNamespace(content=json.dumps({
                'answer_mode': 'category_browse', 'price_intent': 'cheapest',
                'product_query': 'router', 'desired_product_count': 1}))
            current = {'name': 'Current Router', 'price_value': 2500, 'product_type': 'Router'}
            module = SimpleNamespace(lifestore_hybrid_product_search=Mock(return_value={
                'products': [PRODUCTS[0], current]}), lifestore_precise_product_lookup=Mock())
            with patch.object(chat, '_get_lifestore_openai_llm', return_value=llm), \
                 patch.object(chat, '_load_mcp_module', return_value=module), \
                 patch.object(chat, '_write_lifestore_answer', return_value='Current catalog result'), \
                 patch.object(chat, '_maybe_summarize_conversation'):
                result = self.ask(message)
            self.assertEqual(result['products'], [current])
            self.assertEqual(result['tool_name'], 'lifestore_hybrid_product_search')
            module.lifestore_hybrid_product_search.assert_called_once()
            self.assertTrue(module.lifestore_hybrid_product_search.call_args.kwargs['price_candidates'])
            module.lifestore_precise_product_lookup.assert_not_called()

    def test_current_availability_or_purchase_of_historical_product_not_intercepted(self):
        for message in ('is the cheapest router we discussed available now?',
                        'buy the cheapest router we discussed',
                        'what is the cheapest router we discussed priced at now?'):
            self.assertIsNone(historical_price_reference(message, memory.get_conversation_memory('history')))


if __name__ == '__main__':
    unittest.main()
