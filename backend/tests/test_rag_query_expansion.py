from domain.tools.rag_tools import _expand_retrieval_query


def test_distress_loan_amount_query_gets_retrieval_focus():
    expanded = _expand_retrieval_query("What is the distress loan amount?", "hr")

    assert "maximum entitlement" in expanded
    assert "basic salary" in expanded


def test_unrelated_query_is_not_changed():
    query = "How many annual leave days do I have?"

    assert _expand_retrieval_query(query, "hr") == query
