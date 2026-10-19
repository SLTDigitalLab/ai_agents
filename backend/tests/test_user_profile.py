"""Directory enrichment uses synthetic responses; no live Graph calls."""
import unittest
from unittest.mock import AsyncMock, Mock, patch

import test_admin_security  # Establish backend import path.
from core.chat_access import chat_identity
from services import user_profile
import httpx


class UserProfileTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        user_profile._cache.clear()
        self.user = {'tid': 'tenant', 'oid': 'owner', 'email': 'owner@example.test'}
        self.identity = chat_identity(self.user, None)
        self.client = AsyncMock()
        self.response = Mock(status_code=200)
        self.response.json.return_value = {'id': 'owner', 'department': ' HR ', 'jobTitle': ' Manager '}
        self.client.get.return_value = self.response
        self.context = patch.object(user_profile.httpx, 'AsyncClient')
        factory = self.context.start()
        factory.return_value.__aenter__.return_value = self.client
        self.addCleanup(self.context.stop)

    async def test_matching_profile_and_cache_preserve_identity(self):
        result = await user_profile.enrich_chat_identity(self.identity, self.user, 'graph-token')
        self.assertEqual(result.department, 'HR')
        self.assertEqual(result.job_title, 'Manager')
        self.assertEqual(result.principal, self.identity.principal)
        self.assertEqual(result.user_id, self.identity.user_id)
        await user_profile.enrich_chat_identity(self.identity, self.user, 'graph-token')
        self.client.get.assert_awaited_once_with(user_profile.GRAPH_ME, headers={'Authorization': 'Bearer graph-token'})

    async def test_other_users_token_cannot_supply_profile(self):
        self.response.json.return_value['id'] = 'victim'
        result = await user_profile.enrich_chat_identity(self.identity, self.user, 'other-token')
        self.assertEqual(result, self.identity)
        self.assertEqual(len(user_profile._cache), 0)

    async def test_missing_token_and_guests_skip_graph(self):
        for user, token in ((self.user, None), (None, 'token'), (self.user, 'x' * 16385)):
            self.assertEqual(await user_profile.enrich_chat_identity(self.identity, user, token), self.identity)
        self.client.get.assert_not_called()

    async def test_unavailable_or_invalid_graph_response_does_not_block_chat(self):
        for code in (401, 403, 429, 500, 302):
            self.response.status_code = code
            self.assertEqual(await user_profile.enrich_chat_identity(self.identity, self.user, 'token'), self.identity)
        self.response.status_code = 200
        for payload in ([], {}, {'id': 'other'}):
            self.response.json.return_value = payload
            self.assertEqual(await user_profile.enrich_chat_identity(self.identity, self.user, 'token'), self.identity)
        self.client.get.side_effect = httpx.ReadTimeout('synthetic')
        self.assertEqual(await user_profile.enrich_chat_identity(self.identity, self.user, 'token'), self.identity)

    async def test_cache_is_scoped_to_account_and_credential_and_expires(self):
        await user_profile.enrich_chat_identity(self.identity, self.user, 'token')
        other = {**self.user, 'oid': 'other'}
        self.assertEqual(await user_profile.enrich_chat_identity(chat_identity(other, None), other, 'token'), chat_identity(other, None))
        await user_profile.enrich_chat_identity(self.identity, self.user, 'new-token')
        self.assertEqual(self.client.get.await_count, 3)
        with patch.object(user_profile.time, 'monotonic', return_value=10**15):
            await user_profile.enrich_chat_identity(self.identity, self.user, 'token')
        self.assertEqual(self.client.get.await_count, 4)
