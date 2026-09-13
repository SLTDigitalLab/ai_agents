from core.llm import _openai_chat_transport


def test_luna_uses_responses_api_for_tool_calling():
    assert _openai_chat_transport("gpt-5.6-luna") == {
        "use_responses_api": True,
        "reasoning": {"effort": "none"},
    }


def test_other_openai_models_keep_default_transport():
    assert _openai_chat_transport("gpt-4.1-mini") == {}
