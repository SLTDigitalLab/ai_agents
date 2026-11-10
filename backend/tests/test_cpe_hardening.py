import asyncio
import time
from unittest.mock import MagicMock, patch
import pytest
from langchain_core.messages import AIMessage
from langchain_core.documents import Document

from routers.cpe_chat import cpe_chat, CPEChatRequest, CPEChatResponse


@pytest.mark.asyncio
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
         patch("routers.cpe_chat.save_cpe_turn", return_value=None):

        start = time.perf_counter()
        reqs = [
            cpe_chat(CPEChatRequest(message=f"Query {i}", thread_id=f"concurrent_test_thread_{i}"))
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

@pytest.mark.asyncio
async def test_task2_thread_id_generation_unique_in_same_second():
    """Missing thread_id generates uuid.uuid4().hex on server, never time collisions."""
    mock_store = MagicMock()
    mock_store.similarity_search = MagicMock(return_value=[])
    mock_llm = MagicMock()
    mock_llm.ainvoke = MagicMock()

    with patch("routers.cpe_chat.get_cpe_vector_store", return_value=mock_store), \
         patch("routers.cpe_chat.get_chat_model", return_value=mock_llm), \
         patch("routers.cpe_chat.get_cpe_conversation_memory", return_value={"summary": "", "messages": [], "last_device_context": None}), \
         patch("routers.cpe_chat.save_cpe_turn", return_value=None):

        req1 = cpe_chat(CPEChatRequest(message="Hello 1"))
        req2 = cpe_chat(CPEChatRequest(message="Hello 2"))
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


@pytest.mark.asyncio
async def test_task2_get_history_unknown_thread_creates_no_row():
    """GET history of an unknown thread must return empty history and create NO row in DB."""
    import uuid
    from fastapi import HTTPException
    from routers.cpe_chat import cpe_get_history
    from services.cpe_memory import get_cpe_memory_pool

    random_thread = f"test_unknown_{uuid.uuid4().hex[:16]}"
    res = await cpe_get_history(random_thread)
    assert res == {"messages": []}

    # Verify directly in PostgreSQL that no row was created
    pool = get_cpe_memory_pool()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) as count FROM public.cpe_memory WHERE thread_id = %(thread_id)s",
                {"thread_id": random_thread},
            )
            row = cur.fetchone()
            count = row["count"] if isinstance(row, dict) else row[0]
            assert count == 0, f"Expected 0 rows for unknown thread {random_thread}, found {count}"


@pytest.mark.asyncio
async def test_task2_owner_access_control_404_mismatch():
    """User B accessing User A's thread gets 404 (not 403)."""
    import uuid
    from fastapi import HTTPException
    from routers.cpe_chat import cpe_get_history
    from services.cpe_memory import save_cpe_turn

    thread_id = f"test_owned_{uuid.uuid4().hex[:16]}"
    user_a = "user_alpha_123"
    user_b = "user_beta_456"

    # Save a turn owned by user_a
    save_cpe_turn(
        thread_id=thread_id,
        user_message="Hello from user A",
        assistant_message="Response to user A",
        owner_id=user_a,
    )

    # User A can access
    history_a = await cpe_get_history(thread_id, owner_id=user_a)
    assert len(history_a["messages"]) == 2

    # User B accessing User A's thread raises HTTP 404 (never 403)
    with pytest.raises(HTTPException) as exc_info:
        await cpe_get_history(thread_id, owner_id=user_b)
    assert exc_info.value.status_code == 404

