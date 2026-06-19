"""Deterministic, network-free regression tests for the supervisor's rule layer.

These lock in the pure-logic helpers that gate routing and answer handling:
greeting/help/vague detection, clarification resolution, keyword matching, and
the decline/text-normalisation utilities. No embeddings or LLM calls — fast and
safe to run on every change.
"""

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from domain.archetypes import supervisor_agent as sup


# ── general-help vs specialist intent ────────────────────────────────────
@pytest.mark.parametrize(
    "query",
    [
        "hello",
        "Hi!",
        "what can you do?",
        "who are you",
        "which agent should i use",
        "thanks",
    ],
)
def test_general_help_questions_detected(query):
    assert sup._is_general_help_question(query) is True


@pytest.mark.parametrize(
    "query",
    [
        "How do I apply for annual leave in the ERP system?",
        "What is the procedure to call tenders?",
        "My VPN connection is not working",
    ],
)
def test_specialist_questions_are_not_general_help(query):
    assert sup._is_general_help_question(query) is False


# ── bare greeting (ignores prior thread context) ─────────────────────────
@pytest.mark.parametrize("query,expected", [
    ("hello", True),
    ("Good morning!", True),
    ("hi there how do i apply for leave", False),
    ("What is the procedure to call tenders?", False),
])
def test_bare_greeting(query, expected):
    assert sup._is_bare_greeting(query) is expected


# ── vague prompts that should be clarified before routing ────────────────
@pytest.mark.parametrize("query,expected", [
    ("help", True),
    ("I need help with something", True),
    ("can you help me", True),
    ("How do I reset my expired domain password?", False),
    ("What are the responsibilities of the MDRC?", False),
])
def test_vague_specialist_prompt(query, expected):
    assert sup._is_vague_specialist_prompt(query) is expected


# ── clarification-choice resolution ──────────────────────────────────────
@pytest.mark.parametrize("reply,options,expected", [
    ("finance", ["finance", "hr"], "finance"),
    ("it's finance please", ["finance", "hr"], "finance"),
    ("human resources", ["finance", "hr"], "hr"),
    ("something unrelated", ["finance", "hr"], None),   # no match
    ("hr or finance", ["finance", "hr"], None),         # ambiguous → no single resolution
])
def test_resolve_clarification_choice(reply, options, expected):
    assert sup._resolve_clarification_choice(reply, options) == expected


# ── keyword matching feeds the routing boost ─────────────────────────────
def test_matched_keywords_hit_correct_specialist():
    assert "MDRC" in sup._matched_keywords("What are the responsibilities of MDRC?", "finance")
    assert "password reset" in sup._matched_keywords("I need a password reset", "it")


def test_matched_keywords_no_cross_department_leak():
    # A finance keyword query must not match HR's keyword set.
    assert sup._matched_keywords("What are the responsibilities of MDRC?", "hr") == []


# ── decline detection (drives multi-delegate synthesis) ──────────────────
@pytest.mark.parametrize("text", [
    "I don't have that information available.",
    "No relevant documents found.",
    "[KB_UNAVAILABLE] No knowledge base is configured for this agent.",
    "ok",  # too short to be a real answer
])
def test_looks_like_decline_true(text):
    assert sup._looks_like_decline(text) is True


def test_looks_like_decline_false_for_real_answer():
    answer = (
        "All disposals by tender should be carried out through the Procurement "
        "Division, and approval is obtained from the relevant financial authority."
    )
    assert sup._looks_like_decline(answer) is False


# ── text normalisation utilities ─────────────────────────────────────────
def test_collapse_doubled_text():
    assert sup._collapse_doubled_text("Hello world Hello world") == "Hello world"
    assert sup._collapse_doubled_text("Hello world, this is fine") == "Hello world, this is fine"


def test_split_body_and_sources():
    answer = (
        "All disposals by tender go through Procurement.\n\n"
        "Sources: [a.pdf](http://x), [b.pdf](http://y)"
    )
    body, links = sup._split_body_and_sources(answer)
    assert body == "All disposals by tender go through Procurement."
    assert links == ["[a.pdf](http://x)", "[b.pdf](http://y)"]


def test_split_body_and_sources_without_sources():
    body, links = sup._split_body_and_sources("Just an answer, no citations.")
    assert body == "Just an answer, no citations."
    assert links == []


def test_latest_user_query_picks_newest_human_message():
    state = {
        "messages": [
            HumanMessage(content="first question"),
            AIMessage(content="some answer"),
            HumanMessage(content="What is the procedure to call tenders?"),
        ]
    }
    assert sup._latest_user_query(state) == "What is the procedure to call tenders?"
