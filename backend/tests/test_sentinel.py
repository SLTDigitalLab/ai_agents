"""Gateway contract and real LangChain/graph regression tests; no live secrets."""
import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from langchain_core.documents import Document
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# Prevent loading the developer's .env or sending traces during offline tests.
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["LANGSMITH_TRACING"] = "false"
os.environ.setdefault("POSTGRES_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("QDRANT_URL", "http://localhost:6333")

from core.sentinel import SentinelChatModel, SentinelClient, SentinelEmbeddings, SentinelError


def client_for(handler):
    return SentinelClient("https://sentinel.test", "test-virtual-key", transport=httpx.MockTransport(handler))


def chat_response(text="A grounded answer.", finish="stop"):
    return httpx.Response(200, json={"choices": [{"message": {"content": text}, "finish_reason": finish}],
                                     "usage": {"total_tokens": 12}},
                          headers={"x-request-id": "req_test", "x-ai-gateway-selected-tier": "small"})


def test_chat_contract_and_metadata():
    requests = []
    def handler(request):
        requests.append(request)
        return chat_response()
    model = SentinelChatModel(client=client_for(handler))
    result = asyncio.run(model.ainvoke([SystemMessage(content="Answer from evidence."), HumanMessage(content="Hello")]))
    body = json.loads(requests[0].content)
    assert requests[0].url.path == "/v1/chat/completions"
    assert requests[0].headers["authorization"] == "Bearer test-virtual-key"
    assert set(body) == {"model", "messages", "max_tokens", "stream"}
    assert body["model"] == "company-auto" and body["stream"] is False
    assert result.content == "A grounded answer."
    assert result.response_metadata["request_id"] == "req_test"
    assert result.response_metadata["token_usage"]["total_tokens"] == 12
    assert "test-virtual-key" not in repr(model)


@pytest.mark.parametrize("status,code", [(401,"INVALID_KEY"),(403,"MODEL_ALIAS_NOT_ALLOWED"),
    (413,"TOO_LARGE"),(422,"INVALID_FIELD"),(429,"BUDGET_EXCEEDED"),(429,"RATE_LIMITED"),
    (502,"PROVIDER_FAILURE"),(503,"UNAVAILABLE")])
def test_errors_are_safe_and_never_retried(status, code):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"error": {"code": code, "message": "sensitive-provider-text"}},
                              headers={"x-request-id": "req_failure", "retry-after": "5"})
    with pytest.raises(SentinelError) as raised:
        client_for(handler).request("/v1/chat/completions", {})
    assert len(calls) == 1
    assert raised.value.status == status and raised.value.retry_after == "5"
    assert raised.value.request_id == "req_failure"
    assert "sensitive-provider-text" not in str(raised.value)


@pytest.mark.parametrize("error,code", [(httpx.ReadTimeout,"TIMEOUT"),(httpx.ConnectError,"NETWORK_ERROR")])
def test_async_network_failures(error, code):
    def handler(request):
        raise error("private request details", request=request)
    with pytest.raises(SentinelError, match=code):
        asyncio.run(client_for(handler).arequest("/v1/embeddings", {}))


@pytest.mark.parametrize("response", [httpx.Response(502,text="<html>proxy failure</html>"),
    httpx.Response(200,text="not json"), httpx.Response(200,json=[]),
    httpx.Response(200,json={}), httpx.Response(200,json={"choices":[{"message":{"content":None}}]})])
def test_malformed_responses(response):
    with pytest.raises(SentinelError):
        SentinelChatModel(client=client_for(lambda _: response)).invoke("hello")


def test_history_is_text_only_and_original_tool_state_is_unchanged():
    sent = []
    def handler(request):
        sent.append(json.loads(request.content))
        return chat_response()
    history = [HumanMessage(content="Find the leave policy"),
               AIMessage(content="", tool_calls=[{"name":"search","args":{},"id":"call_1"}]),
               ToolMessage(content="[Source: policy | Link: https://example.test] 14 days", tool_call_id="call_1")]
    SentinelChatModel(client=client_for(handler)).invoke(history)
    assert len(sent[0]["messages"]) == 2
    assert sent[0]["messages"][1]["role"] == "user"
    assert "14 days" in sent[0]["messages"][1]["content"]
    assert history[1].tool_calls[0]["id"] == "call_1"
    assert all(set(m) == {"role","content"} for m in sent[0]["messages"])


def test_stream_interface_still_sends_nonstreaming_request():
    sent = []
    def handler(request):
        sent.append(json.loads(request.content))
        return chat_response()
    model = SentinelChatModel(client=client_for(handler))
    async def collect():
        return [m async for m in model.astream("hello")]
    chunks = asyncio.run(collect())
    assert len(chunks) == 1 and chunks[0].content == "A grounded answer."
    assert sent[0]["stream"] is False


def test_structured_classifier_is_validated_text_without_schema_request_fields():
    from domain.guardrails import GuardrailResult
    sent = []
    def handler(request):
        sent.append(json.loads(request.content))
        return chat_response('{"action":"BLOCK","sentiment":"angry"}')
    classifier = SentinelChatModel(client=client_for(handler), model="company-small").with_structured_output(GuardrailResult)
    result = asyncio.run(classifier.ainvoke([HumanMessage(content="a test message")]))
    assert result.action == "BLOCK" and result.sentiment == "angry"
    assert not ({"tools","response_format","tool_choice"} & set(sent[0]))


@pytest.mark.parametrize("text,finish", [('not json','stop'),('{"action":"INVALID"}','stop'),
                                        ('{"action":"PASS"}','length')])
def test_bad_structured_responses_cannot_pass_guardrails(monkeypatch, text, finish):
    from core.config import settings
    import domain.guardrails as guardrails
    monkeypatch.setattr(settings, "GUARDRAIL_PROVIDER", "sentinel")
    model = SentinelChatModel(client=client_for(lambda _: chat_response(text, finish)))
    monkeypatch.setattr(guardrails, "_guardrail_llm", model.with_structured_output(guardrails.GuardrailResult))
    with pytest.raises(SentinelError, match="GUARDRAIL_UNAVAILABLE"):
        asyncio.run(guardrails.classify_intent("test"))


def test_embeddings_batch_order_and_query_use_same_model():
    sent = []
    def handler(request):
        body = json.loads(request.content)
        sent.append(body)
        return httpx.Response(200, json={"data": [
            {"index": i, "embedding": [float(text), 1.0]} for i,text in reversed(list(enumerate(body["input"])))]})
    embeddings = SentinelEmbeddings(client_for(handler), "approved-model", 2, batch_size=2)
    assert embeddings.embed_documents(["1","2","3"]) == [[1.0,1.0],[2.0,1.0],[3.0,1.0]]
    assert asyncio.run(embeddings.aembed_query("4")) == [4.0,1.0]
    assert len(sent) == 3
    assert all(b["model"] == "approved-model" and b["encoding_format"] == "float" for b in sent)
    assert all(set(b) == {"model","input","encoding_format"} for b in sent)


@pytest.mark.parametrize("items", [[], [{"index":0,"embedding":[1]}],
    [{"index":1,"embedding":[1,2]}], [{"index":True,"embedding":[1,2]}],
    [{"index":0,"embedding":[True,2]}], [{"index":0,"embedding":["bad",2]}],
    [{"index":0,"embedding":[1,2]},{"index":0,"embedding":[1,2]}]])
def test_invalid_embedding_count_index_and_dimensions(items):
    embeddings = SentinelEmbeddings(client_for(lambda _: httpx.Response(200,json={"data":items})),"approved-model",2)
    with pytest.raises(SentinelError):
        embeddings.embed_query("test")


def test_nonfinite_vectors_and_duplicate_indexes():
    embeddings = SentinelEmbeddings(client_for(lambda _: None),"approved-model",2)
    for items, count in [([{"index":0,"embedding":[float('nan'),1]}],1),
                         ([{"index":0,"embedding":[1,2]}]*2,2)]:
        with pytest.raises(SentinelError):
            embeddings._vectors(({"data":items},{"request_id":None}),count)


def sentinel_settings(monkeypatch):
    from core.config import settings
    for key,value in {"EMBEDDING_PROVIDER":"sentinel","SENTINEL_GATEWAY_API_KEY":"test-key",
                      "SENTINEL_EMBEDDING_MODEL":"approved-model","SENTINEL_EMBEDDING_DIMENSIONS":2,
                      "SENTINEL_REUSE_EXISTING_VECTORS":False,"SENTINEL_COLLECTION_PREFIX":"sentinel_test_"}.items():
        monkeypatch.setattr(settings,key,value)
    return settings


def test_collection_isolation_and_stable_chunk_ids(monkeypatch):
    settings = sentinel_settings(monkeypatch)
    from core.vector_config import cloud_collection_name, cloud_embedding_dimensions, sentinel_document_ids
    target = cloud_collection_name("finance_docs")
    assert target.startswith("sentinel_test_") and target.endswith("finance_docs")
    assert cloud_collection_name(target) == target
    assert cloud_collection_name("askhrslm_docs") == "askhrslm_docs"
    assert cloud_embedding_dimensions() == 2
    doc = Document(page_content="Solar panels", metadata={"source":"solar.txt"})
    assert sentinel_document_ids([doc]) == sentinel_document_ids([doc])
    assert doc.metadata["embedding_model"] == "approved-model"
    monkeypatch.setattr(settings,"SENTINEL_EMBEDDING_MODEL","another-model")
    assert cloud_collection_name("finance_docs") != target
    monkeypatch.setattr(settings,"SENTINEL_REUSE_EXISTING_VECTORS",True)
    with pytest.raises(ValueError, match="same EMBEDDING_DIMENSIONS"):
        cloud_collection_name("finance_docs")
    monkeypatch.setattr(settings,"EMBEDDING_DIMENSIONS",2)
    assert cloud_collection_name("finance_docs") == "finance_docs"


def test_factories_separate_sentinel_answers_from_direct_tools(monkeypatch):
    from core import llm
    settings = sentinel_settings(monkeypatch)
    monkeypatch.setattr(settings,"LLM_PROVIDER","sentinel")
    monkeypatch.setattr(settings,"LLM_MODEL","direct-tool-model")
    monkeypatch.setattr(settings,"OPENAI_API_KEY","direct-test-key")
    monkeypatch.setattr(settings,"GUARDRAIL_PROVIDER","sentinel")
    monkeypatch.setattr(settings,"ROUTING_EMBEDDING_PROVIDER","sentinel")
    factories = [llm.get_chat_model,llm.get_tool_chat_model,llm.get_guardrail_model,
                 llm.get_embedding_model,llm.get_routing_embedding_model]
    for factory in factories:
        factory.cache_clear()
    try:
        assert llm.get_chat_model().model == "company-auto"
        assert llm.get_guardrail_model().model == "company-small"
        planner = llm.get_tool_chat_model()
        assert planner.model_name == "direct-tool-model"
        assert planner.disable_streaming is True and planner.max_retries == 0
        assert llm.get_embedding_model().model == llm.get_routing_embedding_model().model == "approved-model"
        assert llm.get_embedding_model().dimensions == 2
    finally:
        for factory in factories:
            factory.cache_clear()


def test_tool_decisions_are_preserved_and_final_answer_uses_sentinel(monkeypatch):
    from core import llm
    from core.config import settings
    monkeypatch.setattr(settings,"LLM_PROVIDER","sentinel")
    tool_call = AIMessage(content="",tool_calls=[{"name":"lookup","args":{},"id":"call_1"}])
    planner = AsyncMock()
    planner.ainvoke.return_value = tool_call
    writer = AsyncMock()
    writer.ainvoke.return_value = AIMessage(content="Sentinel answer")
    monkeypatch.setattr(llm,"get_chat_model",lambda:writer)
    assert asyncio.run(llm.invoke_agent_model(planner,[HumanMessage(content="test")])) is tool_call
    writer.ainvoke.assert_not_called()
    planner.ainvoke.return_value = AIMessage(content="private planner draft")
    result = asyncio.run(llm.invoke_agent_model(planner,[HumanMessage(content="test")]))
    assert result.content == "Sentinel answer"
    writer.ainvoke.assert_awaited_once()


def test_model_discovery_and_configuration_errors():
    client = client_for(lambda req: httpx.Response(200,json={"data":[{"id":"company-auto"}]}))
    assert client.request("/v1/models")[0]["data"][0]["id"] == "company-auto"
    for url,key,timeout in [("https://sentinel.test/v1","key",100), ("https://sentinel.test","",100),
                            ("https://sentinel.test","key",0)]:
        with pytest.raises(ValueError):
            SentinelClient(url,key,timeout)
    with pytest.raises(ValueError):
        SentinelEmbeddings(client,"company-auto",3072)
    with pytest.raises(ValueError):
        SentinelEmbeddings(client,"approved-model",None)


def test_example_configuration_allows_chat_without_embedding_details():
    from core.config import Settings
    settings = Settings(_env_file=None, SENTINEL_EMBEDDING_DIMENSIONS="",
                        SENTINEL_GATEWAY_API_KEY="test")
    assert settings.SENTINEL_EMBEDDING_DIMENSIONS is None


def test_staged_ingestion_keeps_existing_query_vectors(monkeypatch):
    from core import llm
    from core.vector_config import cloud_collection_name, cloud_embedding_dimensions, ingestion_collection_name
    settings = sentinel_settings(monkeypatch)
    monkeypatch.setattr(settings,"EMBEDDING_PROVIDER","openai")
    monkeypatch.setattr(settings,"SENTINEL_STAGE_EMBEDDINGS",True)
    assert cloud_collection_name("finance_docs") == "finance_docs"
    target = cloud_collection_name("finance_docs",for_ingestion=True)
    assert target != "finance_docs"
    assert ingestion_collection_name("finance") == ingestion_collection_name("finance_docs") == target
    assert cloud_embedding_dimensions(for_ingestion=True) == 2
    assert llm.get_ingestion_embedding_model().model == "approved-model"
    monkeypatch.setattr(settings,"EMBEDDING_PROVIDER","sentinel")
    monkeypatch.setattr(settings,"SENTINEL_STAGE_EMBEDDINGS",False)
    assert cloud_collection_name("finance_docs") == target


def test_checkout_markers_are_not_changed_or_invented_by_text_writer(monkeypatch):
    from core import llm
    from core.config import settings
    monkeypatch.setattr(settings,"LLM_PROVIDER","sentinel")
    planner, writer = AsyncMock(), AsyncMock()
    monkeypatch.setattr(llm,"get_chat_model",lambda:writer)
    planner.ainvoke.return_value = AIMessage(content="Ready. [RENDER_LIFESTORE_CHECKOUT:order-original]")
    writer.ainvoke.return_value = AIMessage(content="Ready. [RENDER_LIFESTORE_CHECKOUT:invented]")
    answer = asyncio.run(llm.invoke_agent_model(planner,[HumanMessage(content="Checkout")]))
    assert answer.content.endswith("[RENDER_LIFESTORE_CHECKOUT:order-original]")
    assert "invented" not in answer.content
    planner.ainvoke.return_value = AIMessage(content="No checkout was requested.")
    answer = asyncio.run(llm.invoke_agent_model(planner,[HumanMessage(content="Show products")]))
    assert "RENDER_" not in answer.content


def test_synthetic_ingestion_query_and_rag_answer(monkeypatch):
    from qdrant_client import QdrantClient, models
    from core.vector_config import cloud_collection_name, sentinel_document_ids
    sentinel_settings(monkeypatch)
    observed = []
    def handler(request):
        body = json.loads(request.content)
        observed.append(body)
        if request.url.path.endswith("embeddings"):
            return httpx.Response(200,json={"data":[
                {"index":i,"embedding":[1.0,0.0] if "solar" in text.lower() else [0.0,1.0]}
                for i,text in enumerate(body["input"])]})
        assert "solar.txt" in json.dumps(body["messages"])
        return chat_response("Solar panels generate electricity. Sources: [solar.txt](https://example.test/solar)")
    client = client_for(handler)
    embeddings = SentinelEmbeddings(client,"approved-model",2)
    documents = [Document(page_content="Solar panels generate electricity",metadata={"source":"solar.txt"}),
                 Document(page_content="Expense receipts are required",metadata={"source":"expenses.txt"})]
    ids = sentinel_document_ids(documents)
    vectors = embeddings.embed_documents([d.page_content for d in documents])
    store = QdrantClient(":memory:")
    collection = cloud_collection_name("finance_docs")
    try:
        store.create_collection(collection,vectors_config={"dense":models.VectorParams(size=2,distance=models.Distance.COSINE)})
        points = [models.PointStruct(id=pid,vector={"dense":vector},payload={"text":doc.page_content,**doc.metadata})
                  for pid,vector,doc in zip(ids,vectors,documents)]
        store.upsert(collection,points=points)
        store.upsert(collection,points=points)
        assert store.count(collection).count == 2
        query = embeddings.embed_query("What do solar panels do?")
        hits = store.query_points(collection,query=query,using="dense",limit=1).points
        assert hits[0].payload["source"] == "solar.txt"
        response = SentinelChatModel(client=client).invoke([
            ("system","Answer from references, citing sources."),
            ("user",f"References: {hits[0].payload}\nQuestion: What do solar panels do?")])
        assert "Sources:" in response.content
        assert observed[0]["model"] == observed[1]["model"] == "approved-model"
    finally:
        store.close()


def test_actual_kb_graph_and_chat_route_nonstream_fallback(monkeypatch):
    """Real graph, tool binding, checkpoints and route; mock only IO boundaries."""
    import importlib.util
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from langchain_core.tools import tool
    from langchain_openai import ChatOpenAI
    from langgraph.checkpoint.memory import InMemorySaver
    from core import llm
    from core.config import settings
    monkeypatch.setattr(settings,"LLM_PROVIDER","sentinel")
    planner_requests, sentinel_requests = [], []

    @tool
    def search_knowledge_base(query: str) -> str:
        """Look up a synthetic department policy."""
        return "[Source: policy.txt | Link: https://example.test/policy] Annual leave is 14 days."

    def planner_handler(request):
        body = json.loads(request.content)
        planner_requests.append(body)
        assert not body.get("stream",False)
        has_tool = any(m["role"] == "tool" for m in body["messages"])
        message = {"role":"assistant","content":"Hidden planner draft."} if has_tool else {
            "role":"assistant","content":None,"tool_calls":[{"id":"call_test","type":"function",
                "function":{"name":"search_knowledge_base","arguments":'{"query":"annual leave"}'}}]}
        return httpx.Response(200,json={"id":"chat_test","object":"chat.completion","created":0,"model":"direct",
                                       "choices":[{"index":0,"message":message,"finish_reason":"stop" if has_tool else "tool_calls"}]})

    def sentinel_handler(request):
        body = json.loads(request.content)
        sentinel_requests.append(body)
        assert not body["stream"] and "tools" not in body
        assert "14 days" in json.dumps(body["messages"])
        return chat_response("Annual leave is **14 days**. Sources: [policy.txt](https://example.test/policy)")

    planner = ChatOpenAI(model="direct",api_key="test-direct-key",disable_streaming=True,
                         tags=["sentinel_tool_planner"],max_retries=0,
                         http_async_client=httpx.AsyncClient(transport=httpx.MockTransport(planner_handler)))
    writer = SentinelChatModel(client=client_for(sentinel_handler))
    monkeypatch.setattr(llm,"get_tool_chat_model",lambda:planner)
    monkeypatch.setattr(llm,"get_chat_model",lambda:writer)
    monkeypatch.setitem(sys.modules,"domain.tools.rag_tools",SimpleNamespace(search_knowledge_base=search_knowledge_base))
    root = Path(__file__).parents[1]

    def load_module(name,path):
        spec = importlib.util.spec_from_file_location(name,path)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules,name,module)
        spec.loader.exec_module(module)
        return module

    kb = load_module("sentinel_test_kb",root/"domain/archetypes/kb_agent.py")
    memory = InMemorySaver()
    @asynccontextmanager
    async def checkpointer(*args):
        yield memory
    monkeypatch.setitem(sys.modules,"core.checkpointer",SimpleNamespace(
        get_async_postgres_checkpointer=checkpointer,get_postgres_checkpointer=lambda *args:None))
    monkeypatch.setitem(sys.modules,"domain.registry",SimpleNamespace(get_agent_builder=lambda _:kb.build_kb_workflow))
    chat = load_module("sentinel_test_chat",root/"routers/chat.py")
    monkeypatch.setattr(chat,"classify_intent",AsyncMock(return_value=SimpleNamespace(action="PASS",sentiment="neutral",reason=None)))
    app = FastAPI()
    app.include_router(chat.router)
    with TestClient(app) as client:
        for stream in [True,False]:
            response = client.post("/api/v1/chat",json={"message":"What is annual leave?","agent_id":"finance",
                "user_id":"test-user","thread_id":f"thread-{stream}","stream":stream})
            assert response.status_code == 200
            content = response.text if stream else response.json()["response"]
            assert "14 days" in content and "policy.txt" in content
            assert "Hidden planner draft" not in content
        # A follow-up uses checkpointed history, while keeping the same user-facing format.
        response = client.post("/api/v1/chat",json={"message":"And what was the source?","agent_id":"finance",
            "user_id":"test-user","thread_id":"thread-True","stream":True})
        assert response.status_code == 200 and "policy.txt" in response.text
        monkeypatch.setattr(chat,"classify_intent",AsyncMock(side_effect=SentinelError("GUARDRAIL_UNAVAILABLE")))
        for stream in [True,False]:
            failure = client.post("/api/v1/chat",json={"message":"test","agent_id":"finance",
                "user_id":"test-user","thread_id":"failure","stream":stream})
            assert failure.status_code == (200 if stream else 503)
            assert "temporarily unavailable" in failure.text
            assert "GUARDRAIL_UNAVAILABLE" not in failure.text
    assert len(sentinel_requests) == 3
    assert any(m["role"] == "assistant" and "14 days" in m["content"] for m in sentinel_requests[-1]["messages"])

    async def snapshot():
        graph = kb.build_kb_workflow().compile(checkpointer=memory)
        return await graph.aget_state({"configurable":{"thread_id":"thread-True"}})
    state = asyncio.run(snapshot())
    assert any(m.type == "tool" for m in state.values["messages"])
    assert state.values["messages"][-1].content.startswith("Annual leave")
