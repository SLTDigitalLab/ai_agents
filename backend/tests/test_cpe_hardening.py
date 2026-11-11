import asyncio
import time
from unittest.mock import MagicMock, patch
import pytest
from langchain_core.messages import AIMessage
from langchain_core.documents import Document

from routers.cpe_chat import CPEChatRequest, cpe_chat


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_task1_concurrency_acceptance():
    """
    TASK 1 Acceptance Test:
    With a mocked retrieval that sleeps 1s, 10 concurrent requests finish in under 3s total.
    """
    mock_store = MagicMock()

    def slow_similarity_search(*args, **kwargs):
        time.sleep(1.0)
        return [
            Document(
                page_content="Mock CPE technical specs device ONE421 with 148 on hand.",
                metadata={"device": "ONE421", "vendor": "OneAccess", "item_code": "CPEDAT081S", "doc_type": "technical_profile"},
            )
        ]

    mock_store.similarity_search = slow_similarity_search

    mock_llm = MagicMock()
    async def mock_ainvoke(messages, **kwargs):
        await asyncio.sleep(0.05)
        return AIMessage(content="ONE421 has 148 on hand in Peliyagoda Main Stores.")
    mock_llm.ainvoke = mock_ainvoke

    with patch("routers.cpe_chat.get_cpe_vector_store", return_value=mock_store), \
         patch("routers.cpe_chat.get_chat_model", return_value=mock_llm), \
         patch("routers.cpe_chat.get_cpe_conversation_memory", return_value={"summary": "", "messages": [], "last_device_context": None}), \
         patch("routers.cpe_chat.save_cpe_turn", return_value=None), \
         patch("routers.cpe_chat.check_and_refresh_code_index", return_value={}):

        start = time.perf_counter()
        reqs = [
            cpe_chat(CPEChatRequest(message=f"Query {i}", thread_id=f"concurrent_test_thread_{i}"), owner_id="test-owner")
            for i in range(10)
        ]
        results = await asyncio.gather(*reqs)
        duration = time.perf_counter() - start

        assert len(results) == 10
        for res in results:
            assert res.status == "success"
            assert "ONE421" in res.answer

        print(f"\n[TASK 1 Concurrency Test] 10 concurrent requests completed in {duration:.2f}s (Acceptance requirement: < 3.0s)")
        assert duration < 3.0, f"Concurrent execution took {duration:.2f}s, expected < 3.0s"


# ==============================================================================
# TASK 2 TESTS: Thread IDs, Validation & Access Control
# ==============================================================================

@pytest.mark.anyio
async def test_task2_thread_id_generation_unique_in_same_second():
    """Missing thread_id generates uuid.uuid4().hex on server, never time collisions."""
    mock_store = MagicMock()
    mock_store.similarity_search = MagicMock(return_value=[])
    mock_llm = MagicMock()
    mock_llm.ainvoke = MagicMock()

    with patch("routers.cpe_chat.get_cpe_vector_store", return_value=mock_store), \
         patch("routers.cpe_chat.get_chat_model", return_value=mock_llm), \
         patch("routers.cpe_chat.get_cpe_conversation_memory", return_value={"summary": "", "messages": [], "last_device_context": None}), \
         patch("routers.cpe_chat.save_cpe_turn", return_value=None), \
         patch("routers.cpe_chat.check_and_refresh_code_index", return_value={}):

        req1 = cpe_chat(CPEChatRequest(message="Hello 1"), owner_id="test-owner")
        req2 = cpe_chat(CPEChatRequest(message="Hello 2"), owner_id="test-owner")
        res1, res2 = await asyncio.gather(req1, req2)

        assert res1.thread_id is not None
        assert res2.thread_id is not None
        assert res1.thread_id != res2.thread_id
        import re
        assert re.match(r"^[A-Za-z0-9_-]{8,64}$", res1.thread_id)
        assert re.match(r"^[A-Za-z0-9_-]{8,64}$", res2.thread_id)


def test_task2_thread_id_validation_422():
    """Client thread_id must match ^[A-Za-z0-9_-]{8,64}$, otherwise HTTP 422 / ValidationError."""
    from pydantic import ValidationError
    invalid_ids = ["tiny", "has space", "has$pecials!", "a" * 65]
    for invalid_id in invalid_ids:
        with pytest.raises(ValidationError):
            CPEChatRequest(message="valid message", thread_id=invalid_id)

    # Valid thread IDs must pass
    valid_ids = ["12345678", "valid_thread_id_123", "a-b_c-d_123456789"]
    for valid_id in valid_ids:
        req = CPEChatRequest(message="valid message", thread_id=valid_id)
        assert req.thread_id == valid_id


def test_task2_message_max_length_2000():
    """CPEChatRequest.message must enforce max_length=2000."""
    from pydantic import ValidationError
    long_msg = "x" * 2001
    with pytest.raises(ValidationError):
        CPEChatRequest(message=long_msg)

    valid_msg = "x" * 2000
    req = CPEChatRequest(message=valid_msg)
    assert len(req.message) == 2000


def test_cpe_chat_and_history_require_sign_in():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers.cpe_chat import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    assert client.get("/api/v1/cpe/history/test_thread_1234").status_code == 401
    assert client.post("/api/v1/cpe/chat", json={"message": "Check stock"}).status_code == 401


def test_cpe_history_uses_verified_identity():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from core.auth import get_verified_user
    from routers.cpe_chat import router
    from services.cpe_memory import CPEThreadNotFoundError

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_verified_user] = lambda: {"oid": "verified-user"}
    client = TestClient(app)

    with patch("routers.cpe_chat.get_cpe_history_for_frontend", return_value=[]) as read_history:
        response = client.get("/api/v1/cpe/history/test_thread_1234?owner_id=spoofed")
        assert response.json() == {"messages": []}
        read_history.assert_called_once_with("test_thread_1234", None, "verified-user")

    with patch("routers.cpe_chat.get_cpe_history_for_frontend", side_effect=CPEThreadNotFoundError):
        assert client.get("/api/v1/cpe/history/test_thread_1234").status_code == 404


# ==============================================================================
# TASK 3 TESTS: Error Handling & Exception Sanitization
# ==============================================================================

@pytest.mark.anyio
async def test_task3_retrieval_failure_never_leaks_exception():
    """Retrieval errors must return a sanitized message with error_id, never str(exc)."""
    raw_secret_error = "Connection pool exhausted to postgres://admin:secret_pass@10.0.0.1:5432/cpe"
    mock_store = MagicMock()
    mock_store.similarity_search = MagicMock(side_effect=RuntimeError(raw_secret_error))

    with patch("routers.cpe_chat.get_cpe_vector_store", return_value=mock_store), \
         patch("routers.cpe_chat.get_cpe_conversation_memory", return_value={"summary": "", "messages": [], "last_device_context": None}):

        res = await cpe_chat(CPEChatRequest(message="Test retrieval crash", thread_id="error_test_thread_1"), owner_id="test-owner")

        assert res.status == "error"
        # Exception details must NEVER leak to client
        assert raw_secret_error not in res.answer
        assert "secret_pass" not in res.answer
        # Must return generic message + 8-char error_id
        import re
        assert "Error ID:" in res.answer
        match = re.search(r"Error ID:\s*([a-f0-9]{8})", res.answer)
        assert match is not None, f"Expected 8-char error ID in answer: {res.answer}"


@pytest.mark.anyio
async def test_task3_llm_failure_never_leaks_exception():
    """LLM execution errors must return a sanitized message with error_id, never str(exc)."""
    raw_llm_error = "Gemini API 500 Internal Error: ResourceExhausted for tenant 9999"
    mock_store = MagicMock()
    mock_store.similarity_search = MagicMock(return_value=[
        Document(page_content="Mock doc", metadata={"device": "ONE421"})
    ])
    mock_llm = MagicMock()
    mock_llm.ainvoke = MagicMock(side_effect=RuntimeError(raw_llm_error))

    with patch("routers.cpe_chat.get_cpe_vector_store", return_value=mock_store), \
         patch("routers.cpe_chat.get_chat_model", return_value=mock_llm), \
         patch("routers.cpe_chat.get_cpe_conversation_memory", return_value={"summary": "", "messages": [], "last_device_context": None}):

        res = await cpe_chat(CPEChatRequest(message="Test LLM crash", thread_id="error_test_thread_2"), owner_id="test-owner")

        assert res.status == "error"
        assert raw_llm_error not in res.answer
        assert "ResourceExhausted" not in res.answer
        import re
        assert "Error ID:" in res.answer
        match = re.search(r"Error ID:\s*([a-f0-9]{8})", res.answer)
        assert match is not None


@pytest.mark.anyio
async def test_cpe_phone_query_renders_text_blocks_as_markdown():
    markdown = "- **GRP-2602W**\n- **WP820**"
    mock_store = MagicMock()
    mock_store.similarity_search.return_value = [
        Document(page_content="Grandstream IP phone catalog", metadata={})
    ]
    mock_llm = MagicMock()

    async def mock_ainvoke(messages):
        return AIMessage(content=[
            {"type": "text", "text": markdown, "id": "provider-message-id"},
            {"type": "reasoning", "text": "hidden model reasoning"},
        ])

    mock_llm.ainvoke = mock_ainvoke
    with patch("routers.cpe_chat.get_cpe_vector_store", return_value=mock_store), \
         patch("routers.cpe_chat.get_chat_model", return_value=mock_llm), \
         patch("routers.cpe_chat.get_cpe_conversation_memory", return_value={}), \
         patch("routers.cpe_chat.check_and_refresh_code_index", return_value={}), \
         patch("routers.cpe_chat.save_cpe_turn") as save_turn:
        result = await cpe_chat(CPEChatRequest(message="list pi phone"), owner_id="test-owner")

    assert result.status == "success"
    assert result.answer == markdown
    mock_store.similarity_search.assert_called_once_with("list IP phone", k=16)
    assert save_turn.call_args.args[2] == markdown


@pytest.mark.anyio
async def test_task3_guard_failure_falls_back_to_unguarded_answer():
    """If a post-processing guard raises an exception, log it and return unguarded answer."""
    mock_store = MagicMock()
    mock_store.similarity_search = MagicMock(return_value=[
        Document(page_content="Mock doc ONE421 specs", metadata={"device": "ONE421"})
    ])
    mock_llm = MagicMock()
    async def mock_ainvoke(messages, **kwargs):
        return AIMessage(content="The ONE421 router has 148 units on hand.")
    mock_llm.ainvoke = mock_ainvoke

    with patch("routers.cpe_chat.get_cpe_vector_store", return_value=mock_store), \
         patch("routers.cpe_chat.get_chat_model", return_value=mock_llm), \
         patch("routers.cpe_chat.get_cpe_conversation_memory", return_value={"summary": "", "messages": [], "last_device_context": None}), \
         patch("routers.cpe_chat._guard_unsupported_specs", side_effect=Exception("Guard regex crash!")), \
         patch("routers.cpe_chat.save_cpe_turn", return_value=None), \
         patch("routers.cpe_chat.check_and_refresh_code_index", return_value={}):

        res = await cpe_chat(CPEChatRequest(message="ONE421 stock", thread_id="error_test_thread_3"), owner_id="test-owner")

        # Must not crash! Must succeed with unguarded answer
        assert res.status == "success"
        assert "The ONE421 router has 148 units on hand." in res.answer


# ==============================================================================
# TASK 4 TESTS: Lookup-Based Guards & Numerical Spec Guard
# ==============================================================================

def test_task4_guard_unsupported_specs_unit_tests():
    """
    TASK 4 Unit Tests for Numerical Spec Guard:
    - '100 VPN tunnels' with context containing '100 Mbps' -> flagged.
    - '1.2 kg' with context '1.25Gb/s' -> flagged.
    - '0.8 kg' with context '0.8 kg (1.8 lb)' -> not flagged.
    """
    from routers.cpe_chat import _guard_unsupported_specs

    # Test 1: 100 VPN tunnels vs 100 Mbps context -> FLAGGED
    ans1 = "The device supports up to 100 VPN tunnels."
    ctx1 = "OneAccess router IPsec throughput 100 Mbps."
    res1 = _guard_unsupported_specs(ans1, ctx1)
    assert "Grounding Notice" in res1
    assert "100 VPN tunnels" in res1

    # Test 2: 1.2 kg vs 1.25Gb/s context -> FLAGGED
    ans2 = "The chassis weighs 1.2 kg."
    ctx2 = "ESFP optical transceiver 1.25Gb/s single-mode LC."
    res2 = _guard_unsupported_specs(ans2, ctx2)
    assert "Grounding Notice" in res2
    assert "1.2 kg" in res2

    # Test 3: 0.8 kg vs 0.8 kg (1.8 lb) context -> NOT FLAGGED
    ans3 = "The device weight is 0.8 kg."
    ctx3 = "Physical weight: 0.8 kg (1.8 lb) for desktop mount."
    res3 = _guard_unsupported_specs(ans3, ctx3)
    assert "Grounding Notice" not in res3


def test_spec_guard_distinguishes_model_suffix_from_power_rating():
    from routers.cpe_chat import _guard_unsupported_specs

    answer = "The Grandstream GRP-2602W supports 18 W."
    result = _guard_unsupported_specs(answer, "The catalog lists the GRP 2602W.")
    assert "'18 W'" in result
    assert "'2602W'" not in result
    assert _guard_unsupported_specs(answer, "The GRP-2602W supports 18 W.") == answer
    assert "Grounding Notice" in _guard_unsupported_specs(
        "The device consumes 2602 W.", "The catalog lists the GRP-2602W."
    )


def test_task4_validate_item_codes_lookup_and_replacement():
    """
    TASK 4 Item Code Guard:
    (a) Unknown code -> note appended.
    (b) Mismatch with single matching inventory item -> code replaced.
    (c) Matching code -> untouched.
    """
    from routers.cpe_chat import _validate_item_codes, CODE_INDEX

    mock_index = {
        "CPEFTH076S": {"desc": "ZTE ZXHN F670L GPON ONT", "vendor": "ZTE"},
        "CPEFTH077S": {"desc": "HUAWEI EG8145V5 GPON ONT", "vendor": "Huawei"},
        "CPEDAT074": {"desc": "IP PHONE - GRANDSTREAM GRP-2602W", "vendor": "Grandstream"},
        "CPEDAT081S": {"desc": "ONEACCESS ONE421", "vendor": "OneAccess"},
    }

    with patch("routers.cpe_chat.check_and_refresh_code_index", return_value=mock_index):
        # Case A: Unknown code appends note
        res_unknown = _validate_item_codes("Unknown hardware with code CPEDAT999.")
        assert "Stock record for 'CPEDAT999' was not found" in res_unknown

        # Case B: ZTE ONT with phone code CPEDAT074 -> replaced with CPEFTH076S
        res_mismatch = _validate_item_codes("ZTE ZXHN F670L GPON ONT (Item Code: CPEDAT074)")
        assert "CPEFTH076S" in res_mismatch
        assert "CPEDAT074" not in res_mismatch

        # Case C: Valid code untouched
        res_valid = _validate_item_codes("ONE421 router with item code CPEDAT081S.")
        assert "CPEDAT081S" in res_valid
        assert "Verification Notice" not in res_valid


# ==============================================================================
# TASK 5 TESTS: Query Rewriter Acceptance
# ==============================================================================

def test_task5_rewriter_no_facts_injected():
    """Retrieval queries must contain entity names/codes only, never answers/facts."""
    from services.cpe_memory import contextualize_cpe_query

    # Test list queries
    res = contextualize_cpe_query("list all huawei switches")
    assert "NWKEAC118" not in res  # Code list removed from query injection
    assert "148 on hand" not in res

    # Retiring query
    res_retiring = contextualize_cpe_query("which items are marked to be retired in the inventory?")
    assert "21 total" not in res_retiring
    assert "GXP-1782" not in res_retiring


def test_task5_rewriter_pronoun_and_reset_patterns():
    """'which', 'that', 'this' removed from _PRONOUN_PATTERN; 'what about' removed from _RESET_TOPIC_PATTERN."""
    from services.cpe_memory import contextualize_cpe_query

    memory = {
        "messages": [
            {"role": "user", "content": "list all ip phones"},
            {
                "role": "assistant",
                "content": "Here are the Grandstream IP phones...",
                "hardware_cards": [{"name": "Grandstream GRP-2602W", "item_code": "CPEDAT074"}],
            },
        ],
        "last_device_context": {"device": "Grandstream GRP-2602W", "item_code": "CPEDAT074"},
    }

    # "which items are marked to be retired" must NOT resolve to Grandstream phones!
    rewritten = contextualize_cpe_query("which items are marked to be retired in the inventory?", memory_dict=memory)
    assert "Grandstream" not in rewritten
    assert "CPEDAT074" not in rewritten
    assert rewritten == "which items are marked to be retired in the inventory?"

    # "what about the 24 port one" after S5735-L8P4S must keep context
    memory_switch = {
        "messages": [
            {"role": "user", "content": "poe budget of the l8p4s"},
            {
                "role": "assistant",
                "content": "The S5735-L8P4S-A1 has a PoE budget of 124 W.",
                "hardware_cards": [{"name": "Huawei S5735-L8P4S-A1", "item_code": "NWKEAC035"}],
            },
        ],
        "last_device_context": {"device": "Huawei S5735-L8P4S-A1", "item_code": "NWKEAC035"},
    }
    rewritten_switch = contextualize_cpe_query("what about the 24 port one", memory_dict=memory_switch)
    assert "Huawei S5735-L8P4S-A1" in rewritten_switch


# ==============================================================================
# TASK 7 TESTS: Observability & Health Endpoint
# ==============================================================================

@pytest.mark.anyio
async def test_task7_json_log_line_structure(caplog):
    """
    TASK 7 Observability Test:
    Verify one JSON log line per request with:
    request_id, thread_id, retrieval_ms, llm_ms, total_ms, retrieved_count, rewritten_query, guards_fired.
    """
    import json
    import logging

    mock_store = MagicMock()
    mock_store.similarity_search = MagicMock(return_value=[
        Document(page_content="Mock doc ONE421 specs", metadata={"device": "ONE421"})
    ])
    mock_llm = MagicMock()
    async def mock_ainvoke(messages, **kwargs):
        return AIMessage(content="The ONE421 has 148 on hand.")
    mock_llm.ainvoke = mock_ainvoke

    with patch("routers.cpe_chat.get_cpe_vector_store", return_value=mock_store), \
         patch("routers.cpe_chat.get_chat_model", return_value=mock_llm), \
         patch("routers.cpe_chat.get_cpe_conversation_memory", return_value={"summary": "", "messages": [], "last_device_context": None}), \
         patch("routers.cpe_chat.save_cpe_turn", return_value=None), \
         patch("routers.cpe_chat.check_and_refresh_code_index", return_value={}):

        with caplog.at_level(logging.INFO):
            res = await cpe_chat(CPEChatRequest(message="stock of ONE421", thread_id="test_task7_log_thread"), owner_id="test-owner")

        assert res.status == "success"

        json_log = None
        for record in caplog.records:
            try:
                data = json.loads(record.message)
                if "request_id" in data and "retrieval_ms" in data:
                    json_log = data
                    break
            except Exception:
                continue

        assert json_log is not None, f"Expected JSON log line with metrics, captured logs: {[r.message for r in caplog.records]}"
        assert json_log["thread_id"] == "test_task7_log_thread"
        assert isinstance(json_log["request_id"], str) and len(json_log["request_id"]) == 8
        assert json_log["retrieval_ms"] >= 0
        assert json_log["llm_ms"] >= 0
        assert json_log["total_ms"] >= 0
        assert json_log["retrieved_count"] == 1
        assert "stock of ONE421" in json_log["rewritten_query"]
        assert isinstance(json_log["guards_fired"], list)


@pytest.mark.anyio
async def test_task7_guards_fired_tracking(caplog):
    """Verify guards_fired captures 'unsupported_specs' and 'validate_item_codes' when triggered."""
    import json
    import logging

    mock_store = MagicMock()
    mock_store.similarity_search = MagicMock(return_value=[
        Document(page_content="OneAccess router IPsec throughput 100 Mbps.", metadata={"device": "ONE421"})
    ])
    mock_llm = MagicMock()
    async def mock_ainvoke(messages, **kwargs):
        return AIMessage(content="ONE421 supports 100 VPN tunnels.")
    mock_llm.ainvoke = mock_ainvoke

    with patch("routers.cpe_chat.get_cpe_vector_store", return_value=mock_store), \
         patch("routers.cpe_chat.get_chat_model", return_value=mock_llm), \
         patch("routers.cpe_chat.get_cpe_conversation_memory", return_value={"summary": "", "messages": [], "last_device_context": None}), \
         patch("routers.cpe_chat.save_cpe_turn", return_value=None), \
         patch("routers.cpe_chat.check_and_refresh_code_index", return_value={}):

        with caplog.at_level(logging.INFO):
            res = await cpe_chat(CPEChatRequest(message="tunnels of ONE421", thread_id="test_task7_guards_thread"), owner_id="test-owner")

        assert res.status == "success"
        json_log = None
        for record in caplog.records:
            try:
                data = json.loads(record.message)
                if "request_id" in data:
                    json_log = data
                    break
            except Exception:
                continue

        assert json_log is not None
        assert "unsupported_specs" in json_log["guards_fired"]


@pytest.mark.anyio
async def test_task7_health_endpoint():
    """Verify /api/v1/cpe/health reports Qdrant status, point count, and Postgres status."""
    from routers.cpe_chat import cpe_health

    mock_store = MagicMock()
    mock_client = MagicMock()
    mock_alias = MagicMock()
    mock_alias.alias_name = "cpe_docs"
    mock_alias.collection_name = "cpe_docs_v4"
    mock_client.get_aliases.return_value = MagicMock(aliases=[mock_alias])
    mock_client.get_collection.return_value = MagicMock(points_count=632)
    mock_store.client = mock_client

    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    mock_pool.connection.return_value.__enter__.return_value = mock_conn
    mock_pool.get_stats.return_value = {"pool_size": 2, "pool_available": 2}

    with patch("routers.cpe_chat.get_cpe_vector_store", return_value=mock_store), \
         patch("routers.cpe_chat.get_cpe_memory_pool", return_value=mock_pool):

        health = await cpe_health()

        assert health["status"] == "healthy"
        assert health["qdrant"]["reachable"] is True
        assert health["qdrant"]["collection"] == "cpe_docs_v4"
        assert health["qdrant"]["points_count"] == 632
        assert health["postgres"]["reachable"] is True
        assert health["postgres"]["pool_stats"]["pool_size"] == 2


@pytest.mark.anyio
async def test_task7_health_degraded_when_dependency_fails():
    """Verify /api/v1/cpe/health returns 'degraded' when Qdrant is unreachable."""
    from routers.cpe_chat import cpe_health

    with patch("routers.cpe_chat.get_cpe_vector_store", side_effect=RuntimeError("Qdrant unreachable")):
        health = await cpe_health()
        assert health["status"] == "degraded"
        assert health["qdrant"]["reachable"] is False
