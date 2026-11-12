import test from 'node:test';
import assert from 'node:assert/strict';

import { resolveToolResponseLanguage, resolveVoiceLanguage } from './languageDetection.js';

test('romanized Tamil leave balance stays Tamil', () => {
    assert.equal(resolveVoiceLanguage('Ennudaya leave balance evvalavu?'), 'ta-LK');
});

test('completed Tamil transcription remains authoritative over an English tool argument', () => {
    assert.equal(
        resolveToolResponseLanguage('ta-LK', true, 'What is my leave balance?'),
        'ta-LK',
    );
});

test('completed Sinhala transcription remains authoritative over an English tool argument', () => {
    assert.equal(
        resolveToolResponseLanguage('si-LK', true, 'What is my leave balance?'),
        'si-LK',
    );
});

test('tool argument is used only while input transcription is unavailable', () => {
    assert.equal(
        resolveToolResponseLanguage('en-US', false, 'என்னுடைய விடுப்பு எவ்வளவு?'),
        'ta-LK',
    );
});
