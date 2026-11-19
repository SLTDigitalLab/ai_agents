// Per-turn deterministic language resolution for the OpenAI fallback path.
// Gemini repeats this policy server-side, using its final tool argument.
const SINHALA_WORDS = new Set([
    'hari', 'kohomada', 'kohomadha', 'oyaata', 'oyata', 'mata', 'karanna',
    'puluwanda', 'istuti', 'ayubowan', 'mokakda', 'mokadda',
]);
const TAMIL_WORDS = new Set([
    'sari', 'vanakkam', 'enna', 'epadi', 'eppadi', 'sollunga', 'sollu',
    'nandri', 'venum', 'vendum', 'seyyalam', 'seiyalam', 'irukku',
    'ennudaya', 'ennoda', 'evvalavu', 'evalavu', 'enakku', 'solunga', 'irukka',
]);

export function resolveVoiceLanguage(text) {
    const value = String(text || '');
    const sinhala = [...value].filter(char => char >= '\u0D80' && char <= '\u0DFF').length;
    const tamil = [...value].filter(char => char >= '\u0B80' && char <= '\u0BFF').length;
    if (sinhala || tamil) return sinhala >= tamil ? 'si-LK' : 'ta-LK';
    const words = new Set((value.toLowerCase().match(/[a-z]+/g) || []));
    const siHits = [...words].filter(word => SINHALA_WORDS.has(word)).length;
    const taHits = [...words].filter(word => TAMIL_WORDS.has(word)).length;
    if (siHits || taHits) return siHits >= taHits ? 'si-LK' : 'ta-LK';
    return 'en-US';
}

export function resolveToolResponseLanguage(currentTurnLanguage, hasCompletedTranscript, toolQuestion) {
    // Realtime providers may normalize a function argument into English even
    // when the just-completed spoken turn was Tamil or Sinhala. Once an input
    // transcription has completed, it is the authoritative language signal;
    // use the function argument only while that signal is unavailable.
    if (hasCompletedTranscript && currentTurnLanguage) return currentTurnLanguage;
    return resolveVoiceLanguage(toolQuestion);
}

export function languageInstruction(language) {
    const detail = {
        'si-LK': { name: 'Sinhala', nativeName: 'සිංහල', target: 'Sinhala' },
        // Preserve the employee's ta-LK preference while naming the Gemini
        // native-audio Tamil locale in the instruction, not as an API setting.
        'ta-LK': { name: 'Tamil', nativeName: 'தமிழ்', target: 'Tamil (ta-IN)' },
        'en-US': { name: 'English', nativeName: 'English', target: 'en-US' },
    }[language] || { name: 'English', nativeName: 'English', target: 'en-US' };
    return `RESPONSE LANGUAGE FOR THIS TURN: ${detail.name} (${detail.nativeName}). NATIVE AUDIO TARGET: ${detail.target}. RESPOND IN ${detail.name.toUpperCase()}. YOU MUST RESPOND UNMISTAKABLY IN ${detail.name.toUpperCase()}. Use natural spoken ${detail.name} and ${detail.nativeName} script for ordinary language. Do not use English merely because grounded tool facts are English. Preserve exact names, IDs, numbers, tokens, keys, and other factual values.`;
}
