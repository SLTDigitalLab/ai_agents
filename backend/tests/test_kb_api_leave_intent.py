from domain.archetypes.kb_api_agent import (
    _is_other_employee_leave_request,
    _is_personal_leave_data_request,
)


def test_personal_leave_data_intent_in_all_supported_languages():
    for query in (
        "How many leaves have I taken this year?",
        "What is my leave balance?",
        "මේ අවුරුද්දේ මම නිවාඩු දින කීයක් අරගෙන තියෙනවාද?",
        "මගේ නිවාඩු ශේෂය කීයද?",
        "இந்த ஆண்டு நான் எத்தனை விடுப்பு நாட்கள் எடுத்துள்ளேன்?",
        "எனது விடுப்பு இருப்பு என்ன?",
    ):
        assert _is_personal_leave_data_request(query)


def test_general_leave_policy_does_not_use_personal_data_tool():
    for query in (
        "How many annual leave days are employees entitled to?",
        "වාර්ෂික නිවාඩු ප්‍රතිපත්තිය කුමක්ද?",
        "வருடாந்திர விடுப்பு கொள்கை என்ன?",
    ):
        assert not _is_personal_leave_data_request(query)


def test_native_other_employee_request_still_enforces_privacy():
    assert _is_other_employee_leave_request("123456ගේ නිවාඩු ශේෂය කීයද?", "020601")
    assert _is_other_employee_leave_request("123456 விடுப்பு இருப்பு என்ன?", "020601")
