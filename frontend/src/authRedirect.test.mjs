import test from 'node:test';
import assert from 'node:assert/strict';
import { handleAuthRedirect } from './authRedirect.js';

function setup({ path = '/auth/callback', saved = '/workmateai/voice/agent' } = {}) {
  const storage = new Map([['lastAgent', saved], ['msal.cached.account', 'preserve'], ['intentionalLogin', 'true']]);
  const replacements = [];
  const browser = {
    location: { origin: 'http://localhost:3000', pathname: path, replace: p => replacements.push(p) },
    history: { replaceState: (_, __, p) => replacements.push(p) },
    sessionStorage: { getItem: k => storage.get(k), removeItem: k => storage.delete(k) },
  };
  return { browser, storage, replacements };
}

test('missing login request recovers the callback without wiping account storage', async () => {
  const { browser, storage, replacements } = setup();
  const notice = await handleAuthRedirect({ handleRedirectPromise: async () => {
    throw { errorCode: 'no_token_request_cache_error' };
  } }, browser);
  assert.match(notice, /sign in again/);
  assert.deepEqual(replacements, ['/workmateai/voice/agent']);
  assert.equal(storage.get('msal.cached.account'), 'preserve');
  assert.equal(storage.has('intentionalLogin'), false);
});

test('revisiting a callback preserves the login cache', async () => {
  const { browser, storage, replacements } = setup();
  assert.equal(await handleAuthRedirect({ handleRedirectPromise: async () => null }, browser), null);
  assert.equal(storage.get('msal.cached.account'), 'preserve');
  assert.deepEqual(replacements, ['/workmateai/voice/agent']);
});

test('successful login selects the account and returns to the requested agent', async () => {
  const { browser, replacements } = setup();
  const account = { homeAccountId: 'test' };
  let selected;
  await handleAuthRedirect({ handleRedirectPromise: async () => ({ account }), setActiveAccount: a => { selected = a; } }, browser);
  assert.equal(selected, account);
  assert.deepEqual(replacements, ['/workmateai/voice/agent']);
});

test('external and callback return destinations cannot create redirect loops', async () => {
  for (const saved of ['https://another.test/', '//another.test/', '/auth/callback?code=expired']) {
    const { browser, replacements } = setup({ saved });
    await handleAuthRedirect({ handleRedirectPromise: async () => null }, browser);
    assert.deepEqual(replacements, ['/workmateai']);
  }
});

test('ordinary page loads do not navigate and other authentication errors remain errors', async () => {
  const { browser, replacements } = setup({ path: '/workmateai/voice/agent' });
  await handleAuthRedirect({ handleRedirectPromise: async () => null }, browser);
  assert.deepEqual(replacements, []);
  const failure = new Error('Other authentication failure');
  await assert.rejects(handleAuthRedirect({ handleRedirectPromise: async () => { throw failure; } }, browser), failure);
});
