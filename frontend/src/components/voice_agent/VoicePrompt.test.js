import test from 'node:test';
import assert from 'node:assert/strict';

import { SYSTEM_PROMPT } from './constants.js';

test('voice fallback prompt permits Sinhala and Tamil for direct replies', () => {
    assert.match(SYSTEM_PROMPT, /latest completed user audio turn/i);
    assert.match(SYSTEM_PROMPT, /RESPOND IN SINHALA\. YOU MUST\s+RESPOND UNMISTAKABLY IN SINHALA/i);
    assert.match(SYSTEM_PROMPT, /RESPOND IN TAMIL\.\s+YOU MUST\s+RESPOND UNMISTAKABLY IN TAMIL/i);
    assert.match(SYSTEM_PROMPT, /native-audio target is Tamil \(ta-IN\)/i);
    assert.match(SYSTEM_PROMPT, /Never claim that you can only speak\s+English/i);
});
