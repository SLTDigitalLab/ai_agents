import assert from 'node:assert/strict';
import { test } from 'node:test';

test('guest credential persists independently of thread IDs', async () => {
    const values = new Map();
    globalThis.sessionStorage = {
        getItem: key => values.get(key),
        setItem: (key, value) => values.set(key, value),
    };
    const { getGuestChatHeaders } = await import('./chatSession.js?persistent');
    const first = getGuestChatHeaders()['X-Chat-Session'];
    assert.match(first, /^[0-9a-f]{64}$/);
    assert.equal(getGuestChatHeaders()['X-Chat-Session'], first);
    values.set('thread_aiexpo', 'a-public-thread-id');
    assert.equal(getGuestChatHeaders()['X-Chat-Session'], first);
    // A page reload reuses the stored credential.
    const reloaded = await import('./chatSession.js?reload');
    assert.equal(reloaded.getGuestChatHeaders()['X-Chat-Session'], first);
    // An independent browser session generates a distinct credential.
    values.clear();
    const separate = await import('./chatSession.js?separate');
    assert.notEqual(separate.getGuestChatHeaders()['X-Chat-Session'], first);
});

test('blocked iframe storage uses a stable per-page memory credential', async () => {
    globalThis.sessionStorage = {
        getItem: () => { throw new Error('Storage blocked'); },
        setItem: () => { throw new Error('Storage blocked'); },
    };
    const { getGuestChatHeaders } = await import('./chatSession.js?blocked');
    const first = getGuestChatHeaders()['X-Chat-Session'];
    assert.match(first, /^[0-9a-f]{64}$/);
    assert.equal(getGuestChatHeaders()['X-Chat-Session'], first);
});
