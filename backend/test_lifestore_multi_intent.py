"""CB3-09: isolated tracking clauses reuse the existing shopping pipeline."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from routers import lifestore_mcp_chat as chat
from services import lifestore_memory as memory
from lifestore_memory_test_support import install_memory_database


class MultiIntentTests(unittest.TestCase):
    def setUp(self):
        install_memory_database(self)
        memory.clear_conversation('cb309')
        self.addCleanup(memory.clear_conversation, 'cb309')

    def ask(self, message):
        return chat.lifestore_mcp_chat(chat.LifeStoreMCPChatRequest(message=message, thread_id='cb309'))

    def run_shopping(self, message, clause, mode='category_browse', reference=None):
        product = {'name': 'TP-Link Archer C6', 'product_id': 'C6', 'price': 10000}
        module = SimpleNamespace(
            lifestore_hybrid_product_search=Mock(return_value={'products': [product]}),
            lifestore_precise_product_lookup=Mock(return_value={'products': [product]}),
            lifestore_compare_products=Mock(return_value={'products': [product]}))
        plan = {'answer_mode': mode, 'product_query': clause, 'show_product_cards': True,
                'open_lifestore_form': mode == 'purchase'}
        with patch.object(chat, '_load_mcp_module', return_value=module), \
             patch.object(chat, '_plan_lifestore_answer', return_value=plan) as planner, \
             patch.object(chat, '_write_lifestore_answer', return_value='Shopping result') as writer, \
             patch.object(chat, '_maybe_summarize_conversation'):
            result = self.ask(message)
        self.assertEqual(planner.call_args.kwargs['message'], clause)
        self.assertEqual(writer.call_args.kwargs['message'], clause)
        tool = (module.lifestore_precise_product_lookup if mode in {'purchase', 'single_product'}
                else module.lifestore_compare_products if mode == 'comparison'
                else module.lifestore_hybrid_product_search)
        tool.assert_called_once()
        query = tool.call_args.kwargs.get('product_query', tool.call_args.kwargs.get('query'))
        if mode in {'single_product', 'purchase'}:
            self.assertIn('archer c6', query.lower())
            self.assertNotIn('#', query)
            self.assertNotIn('check', query)
        else:
            self.assertEqual(query, clause)
        self.assertEqual(result['products'], [product])
        self.assertIn('Shopping result', result['answer'])
        self.assertEqual(result['answer'], result['reply'])
        self.assertEqual(bool(result['form_payload']), mode == 'purchase')
        if message != clause:
            self.assertIn("can't verify that order reference", result['answer'])
            self.assertEqual(result['order_reference'], reference)
            self.assertEqual(result['order_status_result']['status'], 'unavailable')
        history = memory.get_conversation_memory('cb309')['messages']
        self.assertEqual(history[-2:], [{'role': 'user', 'content': message},
                                       {'role': 'assistant', 'content': result['answer']}])
        return result

    def test_speakers_and_status(self):
        self.run_shopping('show me speakers and check my order status', 'show me speakers')

    def test_numeric_reference_isolated(self):
        self.run_shopping('show me routers and track order #22222222222222', 'show me routers', reference='#22222222222222')

    def test_precise_product_and_status(self):
        self.run_shopping('tell me about TP-Link Archer C6 and check order #ABC123',
                          'tell me about TP-Link Archer C6', 'single_product', '#ABC123')

    def test_reverse_order(self):
        self.run_shopping('check order #ABC123 and show me speakers', 'show me speakers', reference='#ABC123')

    def test_purchase_plus_status(self):
        self.run_shopping('I want to order TP-Link Archer C6 and check order #ABC123',
                          'I want to order TP-Link Archer C6', 'purchase', '#ABC123')

    def test_single_requests_unchanged(self):
        for message, mode in [('show me black and white speakers', 'category_browse'),
                              ('compare router A and router B', 'comparison'),
                              ('show me routers between Rs. 5000 and Rs. 10000', 'category_browse'),
                              ('show me speakers', 'category_browse'),
                              ('I want to order TP-Link Archer C6', 'purchase')]:
            with self.subTest(message=message):
                self.assertIsNone(chat._split_order_and_shopping(message))
                self.run_shopping(message, message, mode)

    def test_internal_conjunction_preserved(self):
        for clause in ['show me black and white speakers', 'compare router A and router B',
                       'show me routers between Rs. 5000 and Rs. 10000']:
            self.assertEqual(chat._split_order_and_shopping(clause + ' and check my order status'),
                             (clause, 'check my order status'))

    def test_standalone_orders_never_load_products(self):
        with patch.object(chat, '_load_mcp_module') as load, patch.object(chat, '_plan_lifestore_answer') as plan:
            for reference in ['#22222222222222', '#qwerty11111111']:
                self.assertEqual(self.ask('check order ' + reference)['order_reference'], reference)
            load.assert_not_called()
            plan.assert_not_called()

    def test_product_failure_still_includes_tracking(self):
        with patch.object(chat, '_load_mcp_module', side_effect=RuntimeError('offline')):
            result = self.ask('show me speakers and check my order status')
        self.assertIn("can't verify", result['answer'])
        self.assertEqual(result['status'], 'mcp_proxy_failed')


if __name__ == '__main__':
    unittest.main()
