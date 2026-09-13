import pytest
from langchain_core.messages import HumanMessage

import domain.archetypes.supervisor_agent as supervisor
from domain.archetypes.supervisor_agent import (
    _detect_query_language,
    _extract_text,
    _is_language_capability_question,
    _native_keyword_route,
    _is_short_follow_up,
    _is_general_help_question,
)


def test_detects_supported_scripts():
    assert _detect_query_language("මගේ නිවාඩු ශේෂය කීයද?") == "si"
    assert _detect_query_language("எனது விடுப்பு இருப்பு என்ன?") == "ta"
    assert _detect_query_language("What is my leave balance?") == "en"


def test_native_hr_queries_route_without_embeddings():
    assert _native_keyword_route("මගේ නිවාඩු ශේෂය කීයද?", "si")[0] == "hr"
    assert _native_keyword_route("எனது விடுப்பு இருப்பு என்ன?", "ta")[0] == "hr"


def test_native_finance_and_it_queries_route_correctly():
    assert _native_keyword_route("මූල්‍ය අයවැය ගැන කියන්න", "si")[0] == "finance"
    assert _native_keyword_route("எனது கணினி கடவுச்சொல் பிரச்சினை", "ta")[0] == "it"


def test_colloquial_loan_transliterations_route_to_hr():
    assert _native_keyword_route("ලෝන් ජාති මොනාද", "si")[0] == "hr"
    assert _native_keyword_route("லோன் வகைகள் என்ன", "ta")[0] == "hr"


@pytest.mark.asyncio
async def test_colloquial_sinhala_loan_query_delegates_without_translation():
    result = await supervisor.route_request(
        {"messages": [HumanMessage(content="ලෝන් ජාති මොනාද")]}
    )

    assert result["routing_action"] == "delegate"
    assert result["routed_agent_id"] == "hr"
    assert result["routing_reason"] == "native_keyword:si:hr"


def test_language_capability_is_supervisor_native():
    assert _is_language_capability_question("Can you speak in Sinhala?")
    assert _is_language_capability_question("ඔබට සිංහල කතා කරන්න පුළුවන්ද?")
    assert _is_language_capability_question("உங்களுக்கு தமிழ் பேச முடியுமா?")


def test_small_talk_is_supervisor_native():
    assert _is_general_help_question("How are you?")
    assert _is_general_help_question("ඔයාට කොහොමද?")
    assert _is_general_help_question("எப்படி இருக்கிறீர்கள்?")


def test_structured_text_preserves_sinhala_boundaries():
    content = [{"text": "කතා කළ හැ"}, {"text": "කියි."}]
    assert _extract_text(content) == "කතා කළ හැකියි."


def test_native_language_follow_ups_keep_specialist_context():
    assert _is_short_follow_up("එතකොට?")
    assert _is_short_follow_up("அடுத்து?")


@pytest.mark.asyncio
async def test_translation_hint_resolves_close_native_scores(monkeypatch):
    async def fake_translation(_query):
        return ("What should I do if I cannot attend work tomorrow?", True, "hr")

    async def fake_scores(_query, _last_agent, _language):
        return [("hr", 0.2812), ("it", 0.2557), ("admin", 0.2207)]

    monkeypatch.setattr(supervisor, "_translate_query_for_routing", fake_translation)
    monkeypatch.setattr(supervisor, "_score_specialists_once", fake_scores)

    scored = await supervisor._score_specialists(
        "මට හෙට වැඩට එන්න බැහැ නම් මොකද කරන්නේ?", None
    )

    assert scored[0] == ("hr", pytest.approx(0.4012))


@pytest.mark.asyncio
async def test_scope_classifier_cannot_zero_semantic_scores(monkeypatch):
    async def fake_translation(_query):
        return ("What is the tallest mountain?", False, None)

    async def fake_scores(_query, _last_agent, _language):
        return [("hr", 0.08), ("admin", 0.06)]

    monkeypatch.setattr(supervisor, "_translate_query_for_routing", fake_translation)
    monkeypatch.setattr(supervisor, "_score_specialists_once", fake_scores)
    scored = await supervisor._score_specialists(
        "ශ්‍රී ලංකාවේ උසම කන්ද කුමක්ද?", None
    )

    assert scored == [("hr", 0.08), ("admin", 0.06)]
