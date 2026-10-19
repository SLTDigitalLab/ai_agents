// Runtime viseme planner for Luma.  It accepts arbitrary generated response
// text; no sentence text is baked into this module or the GLB animation.

const VISEME_WEIGHTS = {
    // Keep visual intensity at roughly 60% of the expressive source shapes.
    // Jaw values are deliberately lower again: it supports vowels rather than
    // bouncing visibly on every syllable.
    A:   { Jaw_Open: 0.23, Viseme_A: 0.62, Mouth_Wide: 0.12 },
    E:   { Jaw_Open: 0.14, Viseme_E: 0.61, Mouth_Wide: 0.37 },
    I:   { Jaw_Open: 0.10, Viseme_I: 0.60, Mouth_Wide: 0.28 },
    O:   { Jaw_Open: 0.18, Viseme_O: 0.62, Mouth_O: 0.44, Mouth_Narrow: 0.13 },
    U:   { Jaw_Open: 0.11, Viseme_U: 0.63, Mouth_O: 0.45, Mouth_Narrow: 0.38 },
    MBP: { Mouth_Closed: 0.64, Viseme_MBP: 0.68 },
    FV:  { Viseme_FV: 0.64, Mouth_Narrow: 0.26 },
    L:   { Jaw_Open: 0.12, Viseme_L: 0.64 },
    S:   { Viseme_S: 0.60, Mouth_Narrow: 0.16 },
    TH:  { Jaw_Open: 0.10, Viseme_TH: 0.64 },
    // Keep rests neutral rather than snapping hard shut between every word.
    REST: { Mouth_Closed: 0.16 },
};

const MORPH_NAMES = Object.freeze([
    'Jaw_Open', 'Mouth_Closed', 'Mouth_Wide', 'Mouth_O', 'Mouth_Narrow', 'Viseme_A', 'Viseme_E', 'Viseme_I',
    'Viseme_O', 'Viseme_U', 'Viseme_MBP', 'Viseme_FV', 'Viseme_L',
    'Viseme_S', 'Viseme_TH',
]);

const weightForViseme = (viseme) => ({ REST: 0.62, MBP: 0.72, FV: 0.78, L: 0.84, S: 0.82, TH: 0.88 }[viseme] || 1.22);

// This is deliberately a lightweight English grapheme-to-viseme mapper, not
// a character-to-open-mouth mapper. It recognizes common multi-letter sounds
// first so words visibly alternate between rounded vowels, wide vowels,
// labials, fricatives and resting shapes.
function tokenizeVisemes(text) {
    const source = String(text || '').toLowerCase();
    const tokens = [];
    let index = 0;
    const add = (viseme, length = 1) => {
        // Collapse adjacent equal shapes (especially runs of unvoiced
        // consonants/rests). This prevents tiny 0 → 1 → 0 pulses while
        // preserving the response's real audio duration.
        const previous = tokens[tokens.length - 1];
        if (previous?.viseme === viseme) previous.weight += weightForViseme(viseme);
        else tokens.push({ viseme, weight: weightForViseme(viseme) });
        index += length;
    };
    while (index < source.length) {
        const rest = source.slice(index);
        const char = source[index];
        if (/\s/.test(char)) { add('REST'); continue; }
        if (/[.,;:!?]/.test(char)) { tokens.push({ viseme: 'REST', weight: 2.4 }); index += 1; continue; }
        if (/^(tion|sion|sh|ch|dge)/.test(rest)) { add('S', rest.startsWith('tion') || rest.startsWith('sion') ? 4 : rest.startsWith('dge') ? 3 : 2); continue; }
        if (rest.startsWith('th')) { add('TH', 2); continue; }
        if (/^(ee|ea|ie|ei)/.test(rest)) { add('I', 2); continue; }
        if (/^(oo|ou|ow)/.test(rest)) { add('U', 2); continue; }
        if (/^(oa|oe|au|aw)/.test(rest)) { add('O', 2); continue; }
        if (/^(ai|ay|ey)/.test(rest)) { add('E', 2); continue; }
        if ('mbp'.includes(char)) { add('MBP'); continue; }
        if ('fv'.includes(char)) { add('FV'); continue; }
        if ('lr'.includes(char)) { add('L'); continue; }
        if ('szx'.includes(char) || (char === 'c' && /[eiy]/.test(source[index + 1] || ''))) { add('S'); continue; }
        if (char === 'o') { add('O'); continue; }
        if ('uwy'.includes(char)) { add('U'); continue; }
        if (char === 'e') { add('E'); continue; }
        if (char === 'i') { add('I'); continue; }
        if (char === 'a') { add('A'); continue; }
        // Unvoiced consonants have no useful dedicated target in this GLB.
        // A short neutral rest is more convincing than forcing an A shape.
        add('REST');
    }
    return tokens;
}

/**
 * Build a mutable, response-specific fallback timeline. `duration` must come
 * from the actual scheduled audio duration when it is available.  A provider
 * with phoneme timestamps can replace this plan directly using the same shape.
 */
export function buildVisemeTimeline(text, duration) {
    const tokens = tokenizeVisemes(text);
    if (!tokens.length || !Number.isFinite(duration) || duration <= 0) return [];
    const totalWeight = tokens.reduce((sum, token) => sum + token.weight, 0);
    let cursor = 0;
    return tokens.map((token) => {
        const span = duration * (token.weight / totalWeight);
        const item = {
            start: cursor,
            end: cursor + span,
            viseme: token.viseme,
        };
        cursor += span;
        return item;
    });
}

export function sampleVisemes(timeline, time) {
    const output = Object.fromEntries(MORPH_NAMES.map((name) => [name, 0]));
    if (!timeline.length || time < 0) return output;
    const currentIndex = timeline.findIndex((item) => time >= item.start && time < item.end);
    const current = timeline[currentIndex];
    if (!current) return output;

    const apply = (item, amount) => {
        const weights = VISEME_WEIGHTS[item?.viseme] || VISEME_WEIGHTS.REST;
        Object.entries(weights).forEach(([name, value]) => { output[name] += value * amount; });
    };
    const span = Math.max(current.end - current.start, 0.001);
    // Broader coarticulation makes short phonemes flow together. It changes
    // only the visual curve; `time` still comes directly from the audio clock.
    const blendWindow = Math.min(0.13, span * 0.42);
    const smoothstep = (value) => {
        const clamped = Math.min(1, Math.max(0, value));
        return clamped * clamped * (3 - (2 * clamped));
    };
    const fromPrevious = currentIndex > 0 && time < current.start + blendWindow
        ? 1 - smoothstep((time - current.start) / blendWindow) : 0;
    const toNext = currentIndex < timeline.length - 1 && time > current.end - blendWindow
        ? smoothstep((time - (current.end - blendWindow)) / blendWindow) : 0;
    apply(current, Math.max(0, 1 - fromPrevious - toNext));
    if (fromPrevious) apply(timeline[currentIndex - 1], fromPrevious);
    if (toNext) apply(timeline[currentIndex + 1], toNext);
    return output;
}

export const lumaMorphNames = MORPH_NAMES;
