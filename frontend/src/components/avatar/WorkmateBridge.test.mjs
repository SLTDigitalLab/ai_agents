import test from 'node:test';
import assert from 'node:assert/strict';
import {
  createWorkmateBridge, NAPSTER_INTERIM_RESULT_MS, VERBATIM_SUFFIX,
} from './WorkmateBridge.js';

const call = (id = 'call-1', args = { user_message: 'What services can you help me with?' }) => ({
  event: 'function_implicitly_called', data: { call_id: id, name: 'answer', arguments: args },
});
function setup(t, overrides = {}) {
  const commands = [], requests = [], errors = [], exchanges = [], diagnostics = [], fatals = [];
  const stops = [], states = [], playback = [], busy = [];
  const bridge = createWorkmateBridge({
    apiUrl: 'https://workmate.test', user: { username: 'employee', name: 'Employee' }, threadId: 'session-1',
    fetchImpl: async (url, options) => {
      requests.push({ url, ...options });
      return { ok: true, json: async () => ({ response: 'Your current leave balance is 12 days.' }) };
    },
    sendCommand: command => commands.push(command), onError: e => errors.push(e),
    onExchange: e => exchanges.push(e), onDiagnostic: e => diagnostics.push(e), onFatal: e => fatals.push(e),
    stopSpeaking: () => stops.push(true), onState: s => states.push(s),
    onSpeechAllowed: allowed => playback.push(allowed), onBusy: value => busy.push(value),
    ...overrides,
  });
  t.after(() => bridge.dispose());
  return { bridge, commands, requests, errors, exchanges, diagnostics, fatals, stops, states, playback, busy };
}

test('waiting toast starts at four seconds and disappears before function output', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let resolve;
  let visible = false;
  const waiting = [];
  const { bridge, commands, exchanges } = setup(t, {
    fetchImpl: () => new Promise(r => { resolve = r; }),
    onWaiting: value => { visible = value; waiting.push(value); },
    onExchange: () => assert.equal(visible, false),
  });
  const pending = bridge.handleEvent(call());
  t.mock.timers.tick(3999);
  assert.equal(visible, false);
  t.mock.timers.tick(1);
  assert.equal(visible, true);
  assert.deepEqual(commands, []);
  assert.deepEqual(exchanges, []);
  resolve({ ok: true, json: async () => ({ response: 'Exact answer.' }) });
  await pending;
  assert.equal(visible, false);
  assert.equal(commands.length, 1);
  assert.equal(commands[0].type, 'send_function_output');
  assert.equal(commands[0].data.call_id, 'call-1');
  assert.equal(commands[0].data.output.message, 'Exact answer.' + VERBATIM_SUFFIX);
  t.mock.timers.tick(4000);
  assert.equal(waiting.filter(Boolean).length, 1);
});

test('fast answers never show the waiting toast', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const waiting = [];
  const { bridge } = setup(t, { onWaiting: value => waiting.push(value) });
  await bridge.handleEvent(call());
  t.mock.timers.tick(4000);
  assert.equal(waiting.includes(true), false);
});

test('response arrival hides the waiting toast before a delayed body is read', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let resolveResponse;
  let resolveBody;
  let visible = false;
  const { bridge } = setup(t, {
    fetchImpl: () => new Promise(resolve => { resolveResponse = resolve; }),
    onWaiting: value => { visible = value; },
  });
  const pending = bridge.handleEvent(call());
  t.mock.timers.tick(4000);
  assert.equal(visible, true);
  resolveResponse({ ok: true, json: () => new Promise(resolve => { resolveBody = resolve; }) });
  await Promise.resolve();
  assert.equal(visible, false);
  resolveBody({ response: 'Exact answer.' });
  await pending;
});

for (const finish of ['failure', 'dispose', 'interrupt', 'provider timeout']) {
  for (const elapsed of [2000, 4000]) {
    test(`${finish} clears waiting notification at ${elapsed}ms without a stale timer`, async t => {
      t.mock.timers.enable({ apis: ['setTimeout'] });
      let reject;
      let visible = false;
      const { bridge } = setup(t, {
        fetchImpl: () => new Promise((_, r) => { reject = r; }),
        onWaiting: value => { visible = value; },
      });
      const pending = bridge.handleEvent(call());
      t.mock.timers.tick(elapsed);
      assert.equal(visible, elapsed === 4000);
      if (finish === 'failure') reject(new TypeError('offline'));
      if (finish === 'dispose') bridge.dispose();
      if (finish === 'interrupt') await bridge.handleEvent({ event: 'message_received', data: {
        message: { role: 'user', action: 'speech_started', item_id: 'new-user' },
      } });
      if (finish === 'provider timeout') await bridge.handleEvent({ event: 'function_call_timeout', data: { call_id: 'call-1' } });
      await pending;
      assert.equal(visible, false);
      t.mock.timers.tick(4000);
      assert.equal(visible, false);
    });
  }
}

test('a superseded request cannot hide the next request waiting toast', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let visible = false;
  const { bridge } = setup(t, {
    fetchImpl: () => new Promise(() => {}), onWaiting: value => { visible = value; },
  });
  const first = bridge.handleEvent(call('first'));
  t.mock.timers.tick(4000);
  const second = bridge.handleEvent(call('second'));
  assert.equal(visible, false);
  t.mock.timers.tick(4000);
  assert.equal(visible, true);
  await first;
  assert.equal(visible, true);
  bridge.dispose();
  await second;
  assert.equal(visible, false);
});

test('customer question -> existing Workmate schema -> exact reference function output', async t => {
  const { bridge, commands, requests, exchanges } = setup(t);
  await bridge.handleEvent(call());
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, 'https://workmate.test/api/v1/chat');
  assert.deepEqual(JSON.parse(requests[0].body), {
    message: 'What services can you help me with?', agent_id: 'supervisor',
    user_id: 'employee', user_name: 'Employee', thread_id: 'session-1', stream: false,
  });
  assert.deepEqual(commands, [{ type: 'send_function_output', data: {
    call_id: 'call-1', output: { success: true, message: 'Your current leave balance is 12 days. [speak verbatim]' }, delay: false,
  } }]);
  assert.equal(exchanges[0].answer, 'Your current leave balance is 12 days.');
});

test('answer whitespace, Unicode, punctuation and formatting are preserved', async t => {
  const answer = '  Leave: **12 days**.\nසිංහල — தமிழ்  ';
  const { bridge, commands } = setup(t, { fetchImpl: async () => ({ ok: true, json: async () => ({ response: answer }) }) });
  await bridge.handleEvent(call('unicode', JSON.stringify({ user_message: 'Leave?' })));
  assert.equal(commands[0].data.output.message, answer + VERBATIM_SUFFIX);
});

test('a display colon after a leading list heading does not disconnect speech', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const answer = '**Your Leave Balance**\n\n- **Annual Leave Plan** — **12** days remaining out of **20**';
  const { bridge, commands, fatals, states } = setup(t, {
    fetchImpl: async () => ({ ok: true, json: async () => ({ response: answer }) }),
  });
  await bridge.handleEvent(call());
  await bridge.handleEvent({ event: 'talk_state_changed', data: { state: 'started' } });
  await bridge.handleEvent({ event: 'message_received', data: { message: {
    role: 'assistant', action: 'completed',
    content: 'Your Leave Balance:\n\n- Annual Leave Plan — 12 days remaining out of 20',
  } } });
  await bridge.handleEvent({ event: 'talk_state_changed', data: { state: 'ended' } });
  t.mock.timers.tick(1500);
  assert.deepEqual(fatals, []);
  assert.equal(states.at(-1), 'Listening...');
  assert.equal(commands[0].data.output.message, answer + VERBATIM_SUFFIX);
});

test('heading content mismatches are diagnostic only', async t => {
  for (const [answer, content] of [
    ['**Balance**\n\n- 12 days', 'Balance:\n\n- 13 days'],
    ['Time: 12:30', 'Time: 1230'],
    ['Ratio\n\n- 1:2', 'Ratio:\n\n- 12'],
  ]) {
    const { bridge, fatals, diagnostics } = setup(t, {
      fetchImpl: async () => ({ ok: true, json: async () => ({ response: answer }) }),
    });
    await bridge.handleEvent(call());
    await bridge.handleEvent({ event: 'message_received', data: { message: {
      role: 'assistant', action: 'completed', content,
    } } });
    assert.equal(fatals.length, 0);
    assert.equal(diagnostics.at(-1).matches, false);
    assert.equal(diagnostics.at(-1).expectedWorkmateResponse, answer);
    assert.equal(diagnostics.at(-1).actualNapsterTranscript, content);
  }
});

test('duplicate deliveries do not repeat either chat request or function output', async t => {
  const { bridge, commands, requests } = setup(t);
  await Promise.all([bridge.handleEvent(call()), bridge.handleEvent(call())]);
  await bridge.handleEvent(call());
  assert.equal(requests.length, 1);
  assert.equal(commands.length, 1);
});

for (const [label, fetchImpl] of [
  ['network', async () => { throw new TypeError('Failed to fetch'); }],
  ['HTTP', async () => ({ ok: false, status: 503 })],
  ['empty answer', async () => ({ ok: true, json: async () => ({ response: '  ' }) })],
  ['wrong response field', async () => ({ ok: true, json: async () => ({ answer: 'wrong' }) })],
  ['invalid JSON', async () => ({ ok: true, json: async () => { throw new SyntaxError(); } })],
  ['null response', async () => ({ ok: true, json: async () => null })],
]) test(`${label} returns failure with matching call ID, never an invented answer`, async t => {
  const { bridge, commands, errors, exchanges } = setup(t, { fetchImpl });
  await bridge.handleEvent(call());
  assert.equal(commands.length, 1);
  assert.equal(commands[0].type, 'send_function_output');
  assert.equal(commands[0].data.call_id, 'call-1');
  assert.equal(commands[0].data.output.success, false);
  assert.equal(commands[0].data.output.message, '');
  assert.ok(errors.at(-1));
  assert.equal(exchanges.length, 0);
});

test('deadline covers fetching and reading the response body', async t => {
  let signal;
  const { bridge, commands } = setup(t, { timeoutMs: 10, fetchImpl: async (_, options) => {
    signal = options.signal;
    return { ok: true, json: () => new Promise(() => {}) };
  } });
  await bridge.handleEvent(call());
  assert.equal(signal.aborted, true);
  assert.equal(commands[0].data.output.message, '');
});

test('missing call ID stops the session and does not fabricate one', async t => {
  const { bridge, commands, requests, fatals } = setup(t);
  const event = call(); delete event.data.call_id;
  await bridge.handleEvent(event);
  assert.equal(requests.length, 0);
  assert.equal(commands.length, 0);
  assert.match(fatals[0], /call ID/);
});

test('missing question and malformed arguments return correlated failures', async t => {
  for (const args of [{}, 'invalid JSON', { user_message: '' }, { user_message: 12 }]) {
    const { bridge, commands, requests } = setup(t);
    await bridge.handleEvent(call('bad', args));
    assert.equal(requests.length, 0);
    assert.equal(commands[0].data.output.success, false);
  }
});

test('expired provider call aborts Workmate and cannot send a late answer', async t => {
  const { bridge, commands, errors, fatals, states } = setup(t, { fetchImpl: () => new Promise(() => {}) });
  const pending = bridge.handleEvent(call());
  await bridge.handleEvent({ event: 'function_call_timeout', data: { call_id: 'call-1' } });
  await pending;
  assert.equal(commands.length, 0);
  assert.equal(fatals.length, 0);
  assert.match(errors.at(-1), /timed out/);
  assert.equal(states.at(-1), 'Microphone muted');
});

test('unmount cancels work with no stale function output', async t => {
  const { bridge, commands } = setup(t, { fetchImpl: () => new Promise(() => {}) });
  const pending = bridge.handleEvent(call());
  bridge.dispose();
  await pending;
  assert.equal(commands.length, 0);
});

test('slow Workmate response uses an interim result then delivers the final answer', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let resolveResponse;
  let signal;
  const answer = 'Your current leave balance is 12 days.';
  const { bridge, commands, fatals } = setup(t, { fetchImpl: (_, options) => {
    signal = options.signal;
    return new Promise(resolve => { resolveResponse = resolve; });
  } });
  const event = call();
  event.type = event.event;
  delete event.event;
  const pending = bridge.handleEvent(event);
  t.mock.timers.tick(NAPSTER_INTERIM_RESULT_MS);
  assert.equal(signal.aborted, false);
  assert.equal(commands.length, 1);
  assert.equal(commands[0].type, 'send_function_output');
  assert.match(commands[0].data.output.message, /still checking/);
  resolveResponse({ ok: true, json: async () => ({ response: answer }) });
  await pending;
  assert.equal(fatals.length, 0);
  assert.equal(commands.length, 2);
  assert.equal(commands[1].type, 'send_message');
  assert.ok(commands[1].data.text.includes(answer));
});

test('timeout after interim result is ignored and final answer is still delivered', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let resolveResponse;
  let signal;
  const { bridge, commands, exchanges, fatals } = setup(t, { fetchImpl: (_, options) => {
    signal = options.signal;
    return new Promise(resolve => { resolveResponse = resolve; });
  } });
  const pending = bridge.handleEvent(call());
  t.mock.timers.tick(NAPSTER_INTERIM_RESULT_MS);
  await bridge.handleEvent({ type: 'function_call_timeout', data: { call_id: 'call-1' } });
  assert.equal(signal.aborted, false);
  resolveResponse({ ok: true, json: async () => ({ response: 'A late Workmate answer.' }) });
  await pending;
  assert.equal(commands.length, 2);
  assert.equal(commands[1].type, 'send_message');
  assert.equal(exchanges.length, 1);
  assert.equal(fatals.length, 0);
});

test('local HTTP deadline is 30 seconds and does not extend the provider call', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let signal;
  const { bridge, commands } = setup(t, { fetchImpl: (_, options) => {
    signal = options.signal;
    return new Promise(() => {});
  } });
  const pending = bridge.handleEvent(call());
  t.mock.timers.tick(29_999);
  assert.equal(signal.aborted, false);
  assert.equal(commands.length, 1);
  t.mock.timers.tick(1);
  await pending;
  assert.equal(signal.aborted, true);
  assert.equal(commands.length, 1);
  assert.equal(commands[0].data.output.success, true);
});

test('provider expiry after output was sent does not disconnect the next turn', async t => {
  const { bridge, commands, diagnostics, fatals } = setup(t);
  await bridge.handleEvent(call());
  await bridge.handleEvent({ event: 'function_call_timeout', data: { call_id: 'call-1' } });
  assert.equal(commands.length, 1);
  assert.equal(fatals.length, 0);
  assert.equal(diagnostics.at(-1).step, 'napster_late_function_timeout_ignored');
  await bridge.handleEvent(call('next'));
  assert.equal(commands.length, 2);
});

test('an unrelated call timeout does not cancel the current response', async t => {
  const { bridge, fatals } = setup(t);
  await bridge.handleEvent(call());
  await bridge.handleEvent({ event: 'function_call_timeout', data: { call_id: 'another-call' } });
  assert.equal(fatals.length, 0);
});

test('unexpected startup speech is muted and cancelled without closing the session', async t => {
  const { bridge, fatals, requests, stops, playback } = setup(t);
  await bridge.handleEvent({ event: 'talk_state_changed', data: { state: 'started' } });
  assert.equal(fatals.length, 0);
  assert.equal(stops.length, 1);
  assert.equal(playback.at(-1), false);
  await bridge.handleEvent(call());
  assert.equal(requests.length, 1);
});

test('rewritten transcript is logged without stopping the conversation', async t => {
  const { bridge, fatals, diagnostics } = setup(t);
  await bridge.handleEvent(call());
  await bridge.handleEvent({ event: 'message_received', data: { message: {
    role: 'assistant', action: 'completed', content: 'You have 12 days remaining.',
  } } });
  assert.equal(fatals.length, 0);
  assert.equal(diagnostics.at(-1).matches, false);
  assert.equal(diagnostics.at(-1).expectedWorkmateResponse, 'Your current leave balance is 12 days.');
  assert.equal(diagnostics.at(-1).actualNapsterTranscript, 'You have 12 days remaining.');
});

test('a transcript mismatch does not interrupt active Napster speech', async t => {
  const { bridge, diagnostics, fatals, playback, states, stops } = setup(t);
  await bridge.handleEvent(call());
  await bridge.handleEvent({ event: 'talk_state_changed', data: { state: 'started' } });
  await bridge.handleEvent({ event: 'message_received', data: { message: {
    role: 'assistant', action: 'completed', content: 'You have 12 days remaining.',
  } } });
  assert.equal(fatals.length, 0);
  assert.equal(stops.length, 0);
  assert.equal(playback.at(-1), true);
  assert.equal(states.at(-1), 'Napster is speaking...');
  assert.equal(diagnostics.at(-1).matches, false);
});

test('matching spoken transcript is logged as verified', async t => {
  const { bridge, fatals, diagnostics } = setup(t);
  await bridge.handleEvent(call());
  await bridge.handleEvent({ event: 'message_received', data: { message: {
    role: 'assistant', action: 'completed', content: 'Your current leave balance is 12 days.',
  } } });
  assert.equal(fatals.length, 0);
  assert.equal(diagnostics.at(-1).matches, true);
});

test('a new question supersedes pending work and never delivers its late result', async t => {
  let resolveResponse;
  let firstSignal;
  let count = 0;
  const { bridge, commands, fatals } = setup(t, { fetchImpl: (_, options) => {
    if (++count === 1) {
      firstSignal = options.signal;
      return new Promise(resolve => { resolveResponse = resolve; });
    }
    return Promise.resolve({ ok: true, json: async () => ({ response: 'The new answer.' }) });
  } });
  const first = bridge.handleEvent(call('first'));
  await bridge.handleEvent(call('next', { user_message: 'A new question' }));
  assert.equal(firstSignal.aborted, true);
  resolveResponse({ ok: true, json: async () => ({ response: 'The obsolete answer.' }) });
  await first;
  await bridge.handleEvent({ event: 'function_call_timeout', data: { call_id: 'first' } });
  assert.equal(fatals.length, 0);
  assert.equal(commands.length, 1);
  assert.equal(commands[0].data.call_id, 'next');
  assert.equal(commands[0].data.output.message, 'The new answer.' + VERBATIM_SUFFIX);
});

test('a new answer call while preparing speech is accepted on the same connection', async t => {
  const { bridge, commands, fatals } = setup(t);
  await bridge.handleEvent(call());
  await bridge.handleEvent(call('next'));
  assert.equal(commands[1].data.call_id, 'next');
  assert.equal(commands[1].data.output.success, true);
  assert.equal(fatals.length, 0);
});

test('user interruption stops playback, listens, and speaks the next exact Workmate answer', async t => {
  const { bridge, commands, requests, fatals, stops, states, playback, busy, diagnostics } = setup(t);
  await bridge.handleEvent(call('first'));
  await bridge.handleEvent({ event: 'message_received', data: { message: {
    role: 'assistant', action: 'delta', response_id: 'old-response', item_id: 'old-item', content: 'Your current',
  } } });
  await bridge.handleEvent({ event: 'talk_state_changed', data: { state: 'started' } });
  await bridge.handleEvent({ event: 'message_received', data: { message: {
    role: 'user', action: 'speech_started', item_id: 'new-user-item',
  } } });
  assert.equal(stops.length, 1);
  assert.equal(playback.at(-1), false);
  assert.equal(states.at(-1), 'Listening...');
  assert.equal(busy.at(-1), false);
  await bridge.handleEvent(call('second', { user_message: 'What about sick leave?' }));
  // Duplicate user interruption and late cancellation must not kill the new turn.
  await bridge.handleEvent({ event: 'message_received', data: { message: {
    role: 'user', action: 'interrupted', item_id: 'new-user-item',
  } } });
  await bridge.handleEvent({ event: 'talk_state_changed', data: { state: 'canceled' } });
  await bridge.handleEvent({ event: 'message_received', data: { message: {
    role: 'assistant', action: 'completed', response_id: 'old-response', item_id: 'old-item', content: 'Your current',
  } } });
  await bridge.handleEvent({ event: 'message_received', data: { message: {
    role: 'assistant', action: 'cancelled', response_id: 'old-response', reason: 'turn_detected',
  } } });
  await bridge.handleEvent({ event: 'talk_state_changed', data: { state: 'started' } });
  await bridge.handleEvent({ event: 'message_received', data: { message: {
    role: 'assistant', action: 'completed', response_id: 'new-response', item_id: 'new-item', content: 'Your current leave balance is 12 days.',
  } } });
  assert.equal(fatals.length, 0);
  assert.equal(commands.length, 2);
  assert.equal(commands[1].data.call_id, 'second');
  assert.equal(commands[1].data.output.message, 'Your current leave balance is 12 days.' + VERBATIM_SUFFIX);
  assert.equal(JSON.parse(requests[1].body).message, 'What about sick leave?');
  assert.equal(JSON.parse(requests[0].body).thread_id, JSON.parse(requests[1].body).thread_id);
  assert.equal(states.at(-1), 'Napster is speaking...');
  assert.equal(playback.at(-1), true);
  assert.equal(diagnostics.at(-1).matches, true);
});

test('provider turn cancellation returns to listening without disconnecting', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const { bridge, fatals, states, playback } = setup(t);
  await bridge.handleEvent(call());
  await bridge.handleEvent({ event: 'message_received', data: { message: {
    role: 'assistant', action: 'cancelled', response_id: 'cancelled-response', reason: 'turn_detected',
  } } });
  t.mock.timers.tick(180_000);
  assert.equal(fatals.length, 0);
  assert.equal(states.at(-1), 'Listening...');
  assert.equal(playback.at(-1), false);
});

test('a cutoff transcript preceding the user interruption does not disconnect', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const { bridge, fatals, states } = setup(t);
  await bridge.handleEvent(call());
  await bridge.handleEvent({ event: 'message_received', data: { message: {
    role: 'assistant', action: 'completed', response_id: 'cutoff', content: 'Your current leave',
  } } });
  assert.equal(fatals.length, 0);
  await bridge.handleEvent({ event: 'talk_state_changed', data: { state: 'ended' } });
  await bridge.handleEvent({ event: 'message_received', data: { message: { role: 'user', action: 'interrupted', item_id: 'interrupt' } } });
  t.mock.timers.tick(180_000);
  assert.equal(fatals.length, 0);
  assert.equal(states.at(-1), 'Listening...');
});

test('an incomplete answer without user interruption remains diagnostic only', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const { bridge, diagnostics, fatals, states } = setup(t);
  await bridge.handleEvent(call());
  await bridge.handleEvent({ event: 'message_received', data: { message: {
    role: 'assistant', action: 'completed', response_id: 'incomplete', content: 'Your current leave',
  } } });
  await bridge.handleEvent({ event: 'talk_state_changed', data: { state: 'ended' } });
  t.mock.timers.tick(1500);
  assert.equal(fatals.length, 0);
  assert.equal(diagnostics.at(-1).incomplete, true);
  assert.equal(states.at(-1), 'Listening...');
});

test('untagged partial transcripts after interruption cannot invalidate a new response', async t => {
  const { bridge, fatals, diagnostics } = setup(t);
  await bridge.handleEvent(call());
  await bridge.handleEvent({ event: 'message_received', data: { message: { role: 'user', action: 'interrupted' } } });
  await bridge.handleEvent(call('next'));
  await bridge.handleEvent({ event: 'message_received', data: { message: {
    role: 'assistant', action: 'completed', content: 'Your current',
  } } });
  assert.equal(fatals.length, 0);
  assert.equal(diagnostics.at(-1).step, 'uncorrelated_transcript_ignored');
});

test('changed new-answer text after an interruption is diagnostic only', async t => {
  const { bridge, commands, diagnostics, fatals } = setup(t);
  await bridge.handleEvent(call());
  await bridge.handleEvent({ event: 'message_received', data: { message: { role: 'user', action: 'interrupted' } } });
  await bridge.handleEvent(call('next'));
  await bridge.handleEvent({ event: 'message_received', data: { message: {
    role: 'assistant', action: 'completed', response_id: 'new-response', content: 'You have 21 days remaining.',
  } } });
  assert.equal(fatals.length, 0);
  assert.equal(diagnostics.at(-1).matches, false);
  assert.equal(diagnostics.at(-1).expectedWorkmateResponse, 'Your current leave balance is 12 days.');
  assert.equal(diagnostics.at(-1).actualNapsterTranscript, 'You have 21 days remaining.');
  assert.equal(commands.at(-1).data.output.message, 'Your current leave balance is 12 days.' + VERBATIM_SUFFIX);
});

test('transcript spacing does not stop playback or alter outgoing text', async t => {
  const answer = '  Your current leave balance\nis 12 days.  ';
  const { bridge, commands, diagnostics, fatals } = setup(t, { fetchImpl: async () => ({ ok: true, json: async () => ({ response: answer }) }) });
  await bridge.handleEvent(call());
  await bridge.handleEvent({ event: 'message_received', data: { message: {
    role: 'assistant', action: 'completed', content: 'Your current leave balance is 12 days.',
  } } });
  assert.equal(commands[0].data.output.message, answer + VERBATIM_SUFFIX);
  assert.equal(fatals.length, 0);
  assert.equal(diagnostics.at(-1).matches, true);
  assert.equal(diagnostics.at(-1).exactMatch, false);
});

test('changed numbers and missing words are logged without disconnecting', async t => {
  for (const content of ['Your current leave balance is 21 days.', 'Your leave balance is 12 days.']) {
    const { bridge, commands, diagnostics, fatals } = setup(t);
    await bridge.handleEvent(call());
    await bridge.handleEvent({ event: 'message_received', data: { message: { role: 'assistant', action: 'completed', content } } });
    assert.equal(fatals.length, 0);
    assert.equal(diagnostics.at(-1).matches, false);
    assert.equal(diagnostics.at(-1).expectedChars, 'Your current leave balance is 12 days.'.length);
    assert.equal(diagnostics.at(-1).transcriptChars, content.length);
    assert.equal(commands[0].data.output.message, 'Your current leave balance is 12 days.' + VERBATIM_SUFFIX);
  }
});

test('plain-text leave balance transcript matches Markdown bold without changing function output', async t => {
  const answer = '**Your Leave Balance**\n\n- **Annual Leave Plan** — **12 days** remaining out of **20**\n- **Sick Leave Plan** — **3 days** remaining out of **7**';
  const transcript = 'Your Leave Balance\n\n- Annual Leave Plan — 12 days remaining out of 20\n- Sick Leave Plan — 3 days remaining out of 7';
  const { bridge, commands, fatals, diagnostics } = setup(t, {
    fetchImpl: async () => ({ ok: true, json: async () => ({ response: answer }) }),
  });
  await bridge.handleEvent(call());
  await bridge.handleEvent({ event: 'message_received', data: { message: {
    role: 'assistant', action: 'completed', response_id: 'leave-response', content: transcript,
  } } });
  assert.equal(fatals.length, 0);
  assert.equal(diagnostics.at(-1).matches, true);
  assert.equal(diagnostics.at(-1).exactMatch, false);
  assert.equal(commands[0].data.output.message, answer + VERBATIM_SUFFIX);
});

test('content mismatches are logged without replacing the canonical Workmate response', async t => {
  for (const [answer, transcript] of [
    ['You have **12 days** remaining.', 'You have 21 days remaining.'],
    ['Contact **HR** for help.', 'Contact IT for help.'],
    ['Sources: [Policy](https://example.test/policy)', 'Sources: Policy'],
    ['Calculate 2**3 + 4**2.', 'Calculate 23 + 42.'],
  ]) {
    const { bridge, commands, diagnostics, fatals } = setup(t, { fetchImpl: async () => ({ ok: true, json: async () => ({ response: answer }) }) });
    await bridge.handleEvent(call());
    await bridge.handleEvent({ event: 'message_received', data: { message: {
      role: 'assistant', action: 'completed', content: transcript,
    } } });
    assert.equal(fatals.length, 0, answer);
    assert.equal(diagnostics.at(-1).matches, false, answer);
    assert.equal(diagnostics.at(-1).expectedWorkmateResponse, answer);
    assert.equal(diagnostics.at(-1).actualNapsterTranscript, transcript);
    assert.equal(commands[0].data.output.message, answer + VERBATIM_SUFFIX);
  }
});

test('other function names do not invoke Workmate', async t => {
  const { bridge, requests, commands } = setup(t);
  const event = call(); event.data.name = 'other';
  await bridge.handleEvent(event);
  assert.equal(requests.length, 0);
  assert.equal(commands.length, 0);
});

test('typed customer question waits for a real call ID and returns only the Workmate answer', async t => {
  const { bridge, commands, requests, exchanges } = setup(t);
  const question = 'How can I reset my password?';
  assert.equal(bridge.sendText(question), true);
  assert.equal(requests.length, 0);
  assert.deepEqual(commands, [{ type: 'send_message', data: {
    role: 'user', text: question, trigger_response: true, delay: false,
  } }]);
  await bridge.handleEvent(call('napster-issued-id', { user_message: 'Password reset?' }));
  assert.equal(requests.length, 1);
  assert.equal(JSON.parse(requests[0].body).message, question);
  assert.equal(commands.length, 2);
  assert.equal(commands[1].type, 'send_function_output');
  assert.equal(commands[1].data.call_id, 'napster-issued-id');
  assert.equal(commands[1].data.output.message, exchanges[0].answer + VERBATIM_SUFFIX);
  assert.equal(commands.filter(c => c.type === 'send_message').length, 1);
});

test('typing cannot submit a second question before the current reply finishes', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const { bridge, commands } = setup(t);
  assert.equal(bridge.sendText('First question'), true);
  assert.equal(bridge.sendText('Duplicate click'), false);
  await bridge.handleEvent(call());
  assert.equal(bridge.sendText('While preparing'), false);
  await bridge.handleEvent({ event: 'talk_state_changed', data: { state: 'started' } });
  assert.equal(bridge.sendText('While speaking'), false);
  await bridge.handleEvent({ event: 'talk_state_changed', data: { state: 'ended' } });
  t.mock.timers.tick(1500);
  assert.equal(bridge.sendText('Next question'), true);
  assert.equal(commands.length, 3);
});

test('typed question fails visibly if Napster never calls answer', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const { bridge, commands, requests, fatals } = setup(t);
  bridge.sendText('A question');
  t.mock.timers.tick(15000);
  assert.match(fatals[0], /did not accept/);
  await bridge.handleEvent(call('late'));
  assert.equal(requests.length, 0);
  assert.equal(commands.length, 1);
});

test('typed input rejects blank, oversized and disconnected submissions', t => {
  const { bridge, commands } = setup(t);
  for (const text of ['', '  ', null, 'x'.repeat(1501)]) assert.equal(bridge.sendText(text), false);
  bridge.dispose();
  assert.equal(bridge.sendText('Question after disconnect'), false);
  assert.equal(commands.length, 0);
});

test('disconnect cancels the pending typed-question timer', t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const { bridge, fatals } = setup(t);
  bridge.sendText('Question');
  bridge.dispose();
  t.mock.timers.tick(15000);
  assert.equal(fatals.length, 0);
});

test('typed input reports a command transport failure', t => {
  const { bridge, fatals, requests } = setup(t, { sendCommand: () => { throw new Error('closed'); } });
  assert.equal(bridge.sendText('Question'), false);
  assert.match(fatals[0], /Unable to send/);
  assert.equal(requests.length, 0);
});
