// Napster implicit answer call -> existing Workmate chat -> function output.
// SDK 1.5 forwards this command unchanged over its data channel.
export const VERBATIM_SUFFIX = ' [speak verbatim]';
// This limits our HTTP request only. It does NOT extend Napster's server-side
// implicit-function deadline. A function_call_timeout event ends the session.
export const WORKMATE_REQUEST_TIMEOUT_MS = 30_000;
export const NAPSTER_INTERIM_RESULT_MS = 7_500;

// Compare spoken text rather than Markdown bold delimiters. Only paired ** at
// word boundaries are formatting; preserve arithmetic, words, numbers, links
// and punctuation within content. A colon after a leading list heading is also
// display-only. This normalization never touches the outgoing answer.
const transcriptText = (text) => text.normalize('NFC')
  .replace(/(^|[^\p{L}\p{N}_\\*])\*\*([^*\s](?:[^*\r\n]*?[^*\s])?)\*\*(?![\p{L}\p{N}_*])/gu, '$1$2')
  .replace(/^([\p{L}][\p{L}\p{N} \t_-]*):(?=\r?\n[ \t]*\r?\n[ \t]*[-*] )/u, '$1')
  .replace(/\s+/gu, ' ').trim();
const messageKeys = (message) => [
  message?.response_id && `response:${message.response_id}`,
  message?.item_id && `item:${message.item_id}`,
].filter(Boolean);

export function createWorkmateBridge({
  apiUrl, user, threadId, sendCommand, fetchImpl = fetch, timeoutMs = WORKMATE_REQUEST_TIMEOUT_MS,
  onState = () => {}, onError = () => {}, onExchange = () => {},
  onBusy = () => {}, onDiagnostic = () => {}, onFatal = () => {},
  stopSpeaking = () => {}, onSpeechAllowed = () => {},
  onWaiting = () => {},
}) {
  const seen = new Set();
  const retiredCalls = new Set();
  const retiredMessages = new Set();
  const currentMessages = new Set();
  const userSpeechItems = new Set();
  let awaitingNewQuestion = false;
  let interruptedOnce = false;
  let ignoreUntaggedStop = false;
  let disposed = false;
  let active = null;
  let pendingText = null;
  let textTimer;
  let expectedSpeech = null;
  let spokenText = '';
  let speechTimer;

  const clearWaiting = (request) => {
    clearTimeout(request?.waitingTimer);
    if (active === request) onWaiting(false);
  };

  const diagnostic = (step, callId, extra = {}) => onDiagnostic({
    step, callId, timestamp: new Date().toISOString(), ...extra,
  });
  const output = (callId, success, message) => {
    if (disposed) return;
    const spokenMessage = message ? message + VERBATIM_SUFFIX : '';
    sendCommand({
      type: 'send_function_output',
      data: { call_id: callId, output: { success, message: spokenMessage }, delay: false },
    });
    diagnostic('function_output_sent', callId, { success, answerChars: message.length });
    diagnostic('final_function_output_sent', callId, { original_call_id: callId, success });
  };
  const deliverDeferredAnswer = (answer, callId) => {
    if (disposed) return;
    sendCommand({
      type: 'send_message',
      data: {
        role: 'system',
        text: `Speak the following Workmate AI answer exactly, without adding, removing, or rephrasing anything:\n\n${answer}`,
        trigger_response: true,
        delay: false,
      },
    });
    diagnostic('deferred_answer_sent', callId, { answerChars: answer.length });
  };
  const clearSpeech = () => {
    clearTimeout(speechTimer);
    expectedSpeech = null;
    spokenText = '';
    onSpeechAllowed(false);
  };
  const clearPendingText = () => {
    clearTimeout(textTimer);
    pendingText = null;
  };
  const fatal = (message) => {
    if (disposed) return;
    disposed = true;
    clearWaiting(active);
    active?.controller.abort();
    clearPendingText();
    clearSpeech();
    onError(message);
    onFatal(message);
  };
  const waitForSpeech = (answer, callId) => {
    clearSpeech();
    expectedSpeech = { answer, callId, started: false };
    onSpeechAllowed(true);
    onState('Napster is preparing a response...');
    speechTimer = setTimeout(() => fatal('Napster did not start speaking. Please reconnect.'), 30000);
  };

  const interruptTurn = (cancelPlayback = true) => {
    const callId = active?.callId || expectedSpeech?.callId;
    if (!callId && pendingText === null) return;
    if (callId) retiredCalls.add(callId);
    for (const key of currentMessages) retiredMessages.add(key);
    currentMessages.clear();
    const request = active;
    clearWaiting(request);
    active = null;
    if (request) {
      request.expired = true;
      request.controller.abort();
    }
    clearPendingText();
    clearSpeech();
    awaitingNewQuestion = true;
    interruptedOnce = true;
    ignoreUntaggedStop = true;
    onBusy(false);
    onError('');
    onState('Listening...');
    diagnostic('answer_interrupted', callId);
    // Mute/clear local playback before cancelling the remote response. Keep the
    // WebRTC connection and microphone alive for the customer's next utterance.
    if (cancelPlayback) stopSpeaking();
  };

  async function handleCall(data) {
    const callId = data?.call_id;
    if (typeof callId !== 'string' || !callId.trim()) {
      fatal('Napster sent a question without a call ID. Please reconnect.');
      return;
    }
    if (seen.has(callId)) { diagnostic('duplicate_ignored', callId); return; }
    seen.add(callId);
    diagnostic('answer_function_received', callId);
    const callStartedAt = performance.now();
    // A new tool call can arrive before speech_started/interrupted. Napster has
    // already advanced to the new turn; do not send cancel against that new call.
    if (active || expectedSpeech) {
      interruptTurn(false);
    }
    awaitingNewQuestion = false;
    currentMessages.clear();
    const typedQuestion = pendingText;
    clearPendingText();
    const controller = new AbortController();
    const request = { callId, controller, expired: false };
    active = request;
    onBusy(true);
    onError('');
    let timer;
    let interimTimer;
    try {
      let args = data.arguments;
      if (typeof args === 'string') {
        try { args = JSON.parse(args); }
        catch { throw new Error('Napster sent an invalid question. Please repeat it.'); }
      }
      // Preserve the actual typed question even if the tool arguments rephrase it.
      const question = typedQuestion ?? args?.user_message;
      if (typeof question !== 'string' || !question.trim()) {
        throw new Error('Napster did not capture your question. Please repeat it.');
      }
      if (question.length > 1500) throw new Error('Please shorten your question to 1,500 characters.');
      onState('Thinking...');
      diagnostic('workmate_request_started', callId, { questionChars: question.length });
      const requestStartedAt = performance.now();
      request.waitingTimer = setTimeout(() => {
        if (!disposed && !request.expired && active === request) onWaiting(true);
      }, 4000);
      const task = (async () => {
        const response = await fetchImpl(`${apiUrl}/api/v1/chat`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: controller.signal,
          body: JSON.stringify({
            message: question, agent_id: 'supervisor', user_id: user.username || 'anonymous',
            user_name: user.name || null, thread_id: threadId, stream: false,
          }),
        });
        diagnostic('workmate_response_headers_received', callId, {
          requestDurationMs: Math.round(performance.now() - requestStartedAt), status: response.status,
        });
        // The HTTP response has arrived; the waiting notice is no longer needed
        // while its body is read and validated.
        clearWaiting(request);
        if (!response.ok) throw new Error(`Workmate AI could not answer (HTTP ${response.status}). Please try again.`);
        let data;
        try { data = await response.json(); }
        catch { throw new Error('Workmate AI returned an invalid response. Please try again.'); }
        if (!data || typeof data.response !== 'string') throw new Error('Workmate AI returned an invalid response. Please try again.');
        if (!data.response.trim()) throw new Error('Workmate AI returned an empty answer. Please try again.');
        return data.response; // Deliberately do not trim, rewrite, or synthesize an answer.
      })();
      interimTimer = setTimeout(() => {
        if (disposed || request.expired || active !== request) return;
        request.deferred = true;
        output(callId, true, "I'm still checking that for you.");
        diagnostic('interim_function_output_sent', callId);
      }, NAPSTER_INTERIM_RESULT_MS);
      const deadline = new Promise((_, reject) => {
        timer = setTimeout(() => {
          reject(new Error('Workmate AI took too long to answer. Please try again.'));
          controller.abort();
        }, timeoutMs);
        controller.signal.addEventListener('abort', () => reject(new Error('Request cancelled.')), { once: true });
      });
      const answer = await Promise.race([task, deadline]);
      if (disposed || request.expired) return;
      clearWaiting(request);
      diagnostic('workmate_answer_received', callId, { answerChars: answer.length });
      diagnostic('workmate_request_completed', callId, {
        requestDurationMs: Math.round(performance.now() - requestStartedAt),
        callDurationMs: Math.round(performance.now() - callStartedAt),
      });
      waitForSpeech(answer, callId);
      if (request.deferred) deliverDeferredAnswer(answer, callId);
      else output(callId, true, answer);
      onExchange({ question, answer, callId });
    } catch (error) {
      if (disposed || request.expired) return;
      clearWaiting(request);
      const message = error instanceof TypeError
        ? 'Unable to reach Workmate AI. Check your network and try again.'
        : error.message || 'Unable to get a Workmate AI answer. Please try again.';
      onError(message);
      try {
        // Resolve the provider call so it does not retry, but keep failures in
        // the page UI. Napster must never improvise or speak when Workmate is down.
        clearSpeech();
        if (!request.deferred) output(callId, false, '');
        onState('Microphone muted');
      } catch { clearSpeech(); fatal('Unable to return the response to Napster. Please reconnect.'); }
    } finally {
      clearTimeout(timer);
      clearTimeout(interimTimer);
      clearWaiting(request);
      if (active === request) active = null;
      if (!disposed && !active && !expectedSpeech && pendingText === null) onBusy(false);
    }
  }

  return {
    sendText(text) {
      if (disposed || active || expectedSpeech || pendingText !== null) return false;
      if (typeof text !== 'string' || !text.trim()) return false;
      if (text.length > 1500) {
        onError('Please shorten your question to 1,500 characters.');
        return false;
      }
      pendingText = text;
      onBusy(true);
      onError('');
      onState('Thinking...');
      // This is the customer's question, never a Workmate-generated answer.
      // Wait for Napster's real answer call ID before invoking Workmate.
      textTimer = setTimeout(() => fatal('The avatar did not accept your typed question. Please reconnect.'), 15000);
      try {
        sendCommand({ type: 'send_message', data: { role: 'user', text, trigger_response: true, delay: false } });
        diagnostic('typed_question_sent', null, { questionChars: text.length });
        return true;
      } catch {
        fatal('Unable to send your typed question. Please reconnect.');
        return false;
      }
    },
    async handleEvent(msg) {
      if (disposed) return;
      const event = msg?.event || msg?.type;
      const data = msg?.data;
      if (event === 'function_implicitly_called' && data?.name === 'answer') {
        await handleCall(data);
      } else if (event === 'function_call_timeout') {
        if (data?.call_id && retiredCalls.has(data.call_id)) return;
        if (active?.deferred && (!data?.call_id || data.call_id === active.callId)) {
          diagnostic('napster_late_function_timeout_ignored', active.callId);
        } else if (active && (!data?.call_id || data.call_id === active.callId)) {
          diagnostic('napster_function_expired', active.callId);
          active.expired = true;
          active.controller.abort();
          onError('Napster timed out waiting for Workmate AI. Please try again.');
          onState('Microphone muted');
          onBusy(false);
        } else if (expectedSpeech && (!data?.call_id || data.call_id === expectedSpeech.callId)) {
          // Workmate already supplied the function output. A late provider
          // timeout must not tear down a completed turn or the next question.
          diagnostic('napster_late_function_timeout_ignored', expectedSpeech.callId);
        }
      } else if (event === 'talk_state_changed') {
        if (messageKeys(data).some(key => retiredMessages.has(key))) return;
        if (data?.state === 'started') {
          if (!expectedSpeech) {
            // Startup/late speech is suppressed without tearing down the session.
            onSpeechAllowed(false);
            stopSpeaking();
            onState(active ? 'Thinking...' : 'Listening...');
            diagnostic('unsolicited_speech_suppressed', null);
            return;
          }
          expectedSpeech.started = true;
          ignoreUntaggedStop = false;
          clearTimeout(speechTimer);
          speechTimer = setTimeout(() => fatal('Napster speech timed out. Please reconnect.'), 180000);
          onState('Napster is speaking...');
          diagnostic('napster_speaking', expectedSpeech.callId);
        } else if (['canceled', 'cancelled'].includes(data?.state)) {
          if (!ignoreUntaggedStop || messageKeys(data).length) interruptTurn(false);
        } else if (expectedSpeech && data?.state === 'ended') {
          if (ignoreUntaggedStop && !expectedSpeech.started && !messageKeys(data).length) return;
          // Transcript events may follow the ended event; retain expected text briefly.
          clearTimeout(speechTimer);
          speechTimer = setTimeout(() => {
            // An incomplete transcript is diagnostic-only, like every other
            // transcript mismatch; it must not end an otherwise live session.
            clearSpeech();
            onBusy(false);
            onState('Listening...');
          }, 1500);
        }
      } else if (event === 'message_received') {
        const message = data?.message;
        if (message?.role === 'user' && ['speech_started', 'interrupted'].includes(message.action)) {
          if (message.item_id && userSpeechItems.has(message.item_id)) return;
          if (message.item_id) userSpeechItems.add(message.item_id);
          interruptTurn();
          onState('Listening...');
          diagnostic('customer_speech_detected', null);
        }
        if (message?.role === 'assistant') {
          const keys = messageKeys(message);
          if (keys.some(key => retiredMessages.has(key))) {
            for (const key of keys) retiredMessages.add(key);
            return;
          }
          if (awaitingNewQuestion) {
            for (const key of keys) retiredMessages.add(key);
            return;
          }
          // An untagged late transcript cannot safely be assigned to a new answer
          // after interruption. Do not mistake it for proof the new answer changed.
          if (interruptedOnce && keys.length === 0) {
            diagnostic('uncorrelated_transcript_ignored', expectedSpeech?.callId || null);
            return;
          }
          for (const key of keys) currentMessages.add(key);
          if (['cancelled', 'canceled'].includes(message.action)) {
            interruptTurn(false);
            return;
          }
        }
        if (message?.role === 'assistant' && expectedSpeech) {
          if (message.action === 'delta' && typeof message.content === 'string') spokenText += message.content;
          if (message.action === 'completed') {
            const actual = typeof message.content === 'string' && message.content ? message.content : spokenText;
            if (actual) {
              const normalizedActual = transcriptText(actual);
              const normalizedExpected = transcriptText(expectedSpeech.answer);
              const matches = normalizedActual === normalizedExpected;
              const incomplete = !matches && normalizedActual.length > 0 && normalizedExpected.startsWith(normalizedActual);
              expectedSpeech.transcriptIncomplete = incomplete;
              diagnostic('napster_transcript_checked', expectedSpeech.callId, {
                matches, incomplete, exactMatch: actual === expectedSpeech.answer,
                expectedChars: expectedSpeech.answer.length, transcriptChars: actual.length,
                ...(!matches && {
                  expectedWorkmateResponse: expectedSpeech.answer,
                  actualNapsterTranscript: actual,
                  normalizedExpected: normalizedExpected,
                  normalizedActual: normalizedActual,
                }),
              });
              // The Workmate answer remains canonical. Napster's transcript is
              // verification data only, so a mismatch must not end the session.
            }
          }
        }
      }
    },
    dispose() {
      disposed = true;
      clearWaiting(active);
      active?.controller.abort();
      clearPendingText();
      clearSpeech();
    },
  };
}
