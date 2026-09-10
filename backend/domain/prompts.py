"""Shared prompt fragments used across agent archetypes."""

# Appended to every user-facing system prompt so the model answers in the
# user's own language. SLTMobitel users write in English, Sinhala, or Tamil,
# and previously the model only emergently matched the language (so short turns
# like "thanks" drifted to English). This makes the behavior explicit.
#
# Carve-outs: the literal "Sources:" label is parsed by the frontend, the exact
# decline sentinel is matched by the supervisor's decline detection, and
# acronyms/URLs/filenames must stay verbatim — so those are kept unchanged
# regardless of the response language.
LANGUAGE_RULE = """LANGUAGE (CRITICAL):
- Detect the language of the user's LATEST message and write your ENTIRE response in that SAME language. SLTMobitel users commonly write in English, Sinhala (සිංහල), or Tamil (தமிழ்).
- This applies to everything you generate: greetings, thank-you replies, small talk, the direct answer, bullet points, and tone. If the user writes in Sinhala, reply fully in Sinhala; if Tamil, reply fully in Tamil; if English, reply in English.
- Match the CURRENT message's language even if earlier turns used a different language. For a mixed-language message, use the dominant language.
- Keep these UNCHANGED regardless of the response language: source filenames and URLs, the literal label "Sources:", the exact phrase "I don't have that information available.", and technical identifiers/acronyms (e.g. ERP, EPF, OTL, PAYE, DMS, NIT, TDC).
- Numbers, dates, and currency amounts keep their digits; translate only the surrounding words."""
