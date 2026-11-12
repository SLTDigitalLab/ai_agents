import test from 'node:test';
import assert from 'node:assert/strict';

import {
    needsAudioDrivenLipSync,
    sampleAudioDrivenVisemes,
} from './LumaLipSync.js';

test('English keeps the text-viseme path', () => {
    assert.equal(needsAudioDrivenLipSync('Your leave balance is fourteen days.'), false);
});

test('Sinhala and Tamil select playback-audio lip sync', () => {
    assert.equal(needsAudioDrivenLipSync('ඔබේ නිවාඩු දින දහහතරයි.'), true);
    assert.equal(needsAudioDrivenLipSync('உங்கள் விடுப்பு இருப்பு பதினான்கு நாட்கள்.'), true);
});

test('audio fallback opens only for audible speech and closes in silence', () => {
    const speaking = sampleAudioDrivenVisemes(0.6, 0.2);
    const silent = sampleAudioDrivenVisemes(0, 0.2);
    assert.ok(speaking.Jaw_Open > 0);
    assert.ok(speaking.Viseme_A > 0);
    assert.equal(silent.Jaw_Open, 0);
    assert.equal(silent.Viseme_A, 0);
});
