"""Storage contract tests; psycopg boundary is mocked, no database required."""
import sys
import traceback
import unittest
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from services import lifestore_memory as memory
from lifestore_memory_test_support import install_memory_database


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.db = install_memory_database(self)

    def test_empty_shape_and_detached_reads(self):
        expected = {'summary': '', 'messages': [], 'state': {
            'current_category': None, 'last_products_shown': [],
            'last_selected_product': None, 'pending_action': None}}
        self.assertEqual(memory.get_conversation_memory('new'), expected)
        result = memory.get_conversation_memory('new')
        result['messages'].append({'role': 'user', 'content': 'not saved'})
        self.assertEqual(memory.get_conversation_memory('new'), expected)
        self.assertIn(('ask_lifestore', 'new'), self.db.rows)

    def test_messages_order_retention_and_restart(self):
        memory.save_message('a', 'user', 'hello')
        memory.save_message('a', 'assistant', 'hi')
        memory._table_created = False  # only process-local cache; rows remain in DB
        self.assertEqual(memory.get_conversation_memory('a')['messages'], [
            {'role': 'user', 'content': 'hello'}, {'role': 'assistant', 'content': 'hi'}])
        for i in range(25):
            memory.save_message('a', 'user', str(i))
        self.assertEqual([m['content'] for m in memory.get_conversation_memory('a')['messages']],
                         [str(i) for i in range(5, 25)])

    def test_structured_state_and_compact_products(self):
        products = [{'product_id': str(i), 'name': str(i), 'price_value': i,
                     'description': 'must not persist'} for i in range(10)]
        memory.set_current_category('a', 'router')
        memory.set_current_category('a', None)
        memory.set_pending_action('a', 'purchase')
        memory.set_last_selected_product('a', products[0])
        memory.save_products_shown('a', products)
        state = memory.get_conversation_memory('a')['state']
        self.assertEqual(state['current_category'], 'router')
        self.assertEqual(state['pending_action'], 'purchase')
        self.assertEqual(len(state['last_products_shown']), 8)
        self.assertIsNone(state['last_selected_product'])
        self.assertNotIn('description', state['last_products_shown'][0])
        memory.save_products_shown('a', [products[1]])
        memory.save_products_shown('a', [])
        state = memory.get_conversation_memory('a')['state']
        self.assertEqual(state['last_selected_product']['product_id'], '1')
        self.assertEqual(len(state['last_products_shown']), 8)
        memory.set_last_selected_product('a', None)
        memory.set_pending_action('a', None)
        self.assertIsNone(memory.get_conversation_memory('a')['state']['last_selected_product'])
        self.assertIsNone(memory.get_conversation_memory('a')['state']['pending_action'])

    def test_summary_threshold_payload_and_atomic_apply(self):
        memory.set_summary('a', ' old ')
        memory.set_current_category('a', 'router')
        for i in range(14):
            memory.save_message('a', 'user', str(i))
        self.assertIsNone(memory.get_summarization_payload('a'))
        memory.save_message('a', 'assistant', '14')
        payload = memory.get_summarization_payload('a')
        self.assertEqual(payload['existing_summary'], 'old')
        self.assertEqual(len(payload['messages_to_summarize']), 9)
        self.assertEqual(len(payload['recent_messages']), 6)
        self.assertIsNone(memory.get_summarization_payload('a', keep_recent=0))
        before = memory.get_conversation_memory('a')['state']
        memory.apply_conversation_summary('a', ' new ', payload['recent_messages'])
        after = memory.get_conversation_memory('a')
        self.assertEqual(after['summary'], 'new')
        self.assertEqual(after['messages'], payload['recent_messages'])
        self.assertEqual(after['state'], before)
        memory.apply_conversation_summary('a', ' ', [])
        self.assertEqual(memory.get_conversation_memory('a'), after)

    def test_summary_preserves_intervening_append_and_rejects_stale_snapshot(self):
        for i in range(15):
            memory.save_message('a', 'user', str(i))
        payload = memory.get_summarization_payload('a')
        memory.save_message('a', 'assistant', 'new')
        memory.apply_conversation_summary('a', 'summary', payload['recent_messages'])
        self.assertEqual(len(memory.get_conversation_memory('a')['messages']), 7)
        before = memory.get_conversation_memory('a')
        memory.apply_conversation_summary('a', 'stale', [{'role': 'user', 'content': 'missing'}])
        self.assertEqual(memory.get_conversation_memory('a'), before)

    def test_missing_ids_never_connect(self):
        with patch.object(memory.psycopg, 'connect') as connect:
            for thread in (None, ''):
                self.assertEqual(memory.get_conversation_memory(thread), memory._empty_conversation())
                memory.save_message(thread, 'user', 'x')
                memory.save_products_shown(thread, [{}])
                memory.set_current_category(thread, 'router')
                memory.set_last_selected_product(thread, {})
                memory.set_pending_action(thread, 'buy')
                memory.set_summary(thread, 'summary')
                memory.apply_conversation_summary(thread, 'summary', [])
                memory.clear_conversation(thread)
                self.assertEqual(memory.get_summary(thread), '')
                self.assertIsNone(memory.get_summarization_payload(thread))
            connect.assert_not_called()

    def test_threads_and_delete_are_isolated(self):
        memory.save_message('a', 'user', 'a')
        memory.save_message('b', 'user', 'b')
        memory.clear_conversation('a')
        self.assertEqual(memory.get_conversation_memory('a')['messages'], [])
        self.assertEqual(memory.get_conversation_memory('b')['messages'][0]['content'], 'b')

    def test_concurrent_writes_use_lock_and_do_not_lose_messages(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda i: memory.save_message('a', 'user', str(i)), range(20)))
        messages = memory.get_conversation_memory('a')['messages']
        self.assertEqual({m['content'] for m in messages}, {str(i) for i in range(20)})
        selects = [s for s in self.db.statements if s.startswith('SELECT summary')]
        self.assertTrue(all(s.endswith('FOR UPDATE') for s in selects[:-1]))

    def test_failed_write_rolls_back_and_sanitizes_error(self):
        memory.save_message('a', 'user', 'original')
        before = deepcopy(self.db.rows)
        self.db.fail_update = True
        try:
            memory.save_message('a', 'user', 'lost')
            self.fail('expected storage error')
        except memory.MemoryStorageError:
            self.assertNotIn('secret-password', traceback.format_exc())
        self.assertEqual(self.db.rows, before)
        with self.assertRaises(memory.MemoryStorageError):
            memory.set_summary('new', 'failure')
        self.assertNotIn(('ask_lifestore', 'new'), self.db.rows)

    def test_initializer_failure_retries_and_read_failure_is_explicit(self):
        with patch.object(memory.psycopg, 'connect', side_effect=RuntimeError('secret')):
            with self.assertRaises(memory.MemoryStorageError):
                memory.get_conversation_memory('a')
        self.assertFalse(memory._table_created)
        memory.ensure_memory_table()
        memory.ensure_memory_table()
        self.assertEqual(sum(s.startswith('CREATE TABLE') for s in self.db.statements), 1)
        with patch.object(memory.psycopg, 'connect', side_effect=RuntimeError('secret')):
            with self.assertRaises(memory.MemoryStorageError):
                memory.get_summary('a')


if __name__ == '__main__':
    unittest.main()
