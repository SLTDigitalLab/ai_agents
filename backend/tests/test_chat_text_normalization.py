from routers.chat import _message_content_to_text, _sanitize_response_script


def test_structured_stream_content_preserves_boundary_whitespace():
    chunks = [
        [{"type": "text", "text": "You can "}],
        [{"type": "text", "text": "apply for a loan.\n\n"}],
        [{"type": "text", "text": "- Select the loan type."}],
    ]

    result = "".join(
        _message_content_to_text(chunk, strip=False) for chunk in chunks
    )

    assert result == (
        "You can apply for a loan.\n\n"
        "- Select the loan type."
    )


def test_stored_structured_content_still_repairs_missing_word_boundaries():
    content = [
        {"type": "text", "text": "You can"},
        {"type": "text", "text": "apply for a loan."},
    ]

    assert _message_content_to_text(content) == "You can apply for a loan."


def test_sinhala_response_cannot_contain_tamil_or_devanagari_script():
    contaminated = "නිවාඩුව සඳහා अनुपस्थितියක් සහ தமிழ் වචනයක්"
    cleaned = _sanitize_response_script(contaminated, "නිවාඩුවක් ගන්නේ කොහොමද?")

    assert not any("\u0900" <= char <= "\u097f" for char in cleaned)
    assert not any("\u0b80" <= char <= "\u0bff" for char in cleaned)
    assert "නිවාඩුව සඳහා" in cleaned


def test_tamil_response_cannot_contain_sinhala_or_devanagari_script():
    contaminated = "விடுப்பு සඳහා हिन्दी சொல்"
    cleaned = _sanitize_response_script(contaminated, "விடுப்பு எடுப்பது எப்படி?")

    assert not any("\u0d80" <= char <= "\u0dff" for char in cleaned)
    assert not any("\u0900" <= char <= "\u097f" for char in cleaned)
    assert "விடுப்பு" in cleaned
