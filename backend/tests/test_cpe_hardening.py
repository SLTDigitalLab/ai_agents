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
