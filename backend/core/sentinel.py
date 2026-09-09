"""Sentinel's documented text/embedding contract, without SDK field expansion.

No automatic retries: a failed or timed-out request may already have incurred
provider cost. Tool execution stays in Workmate's existing direct-provider graph.
"""
from __future__ import annotations

import json
import logging
import math
import re
from typing import Any

import httpx
from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda
from pydantic import Field

log = logging.getLogger(__name__)


class SentinelError(RuntimeError):
    """Safe diagnostics only; never embed upstream messages or request bodies."""

    def __init__(self, code: str, *, status=None, request_id=None, retry_after=None):
        self.code = _safe_diagnostic(code) or "SENTINEL_ERROR"
        self.status = status
        self.request_id = _safe_diagnostic(request_id)
        self.retry_after = retry_after
        super().__init__(f"Sentinel {self.code} (status={status}, request_id={self.request_id})")

    @property
    def public_message(self):
        if self.code == "TIMEOUT":
            return "The AI service took too long to respond. Please try again."
        if self.status == 429:
            return "The AI service has reached a usage or capacity limit. Please try later or contact support."
        return "The AI service is temporarily unavailable. Please try again or contact support."


def _safe_diagnostic(value):
    if isinstance(value, str) and re.fullmatch(r"[\w.:-]{1,160}", value):
        return value
    return None


class SentinelClient:
    def __init__(self, url, api_key, timeout_ms=130000, *, transport=None):
        self.url = url.rstrip("/")
        parsed = httpx.URL(self.url)
        if parsed.scheme not in {"https", "http"} or not parsed.host or parsed.path not in {"", "/"}:
            raise ValueError("SENTINEL_GATEWAY_URL must be an origin without /v1")
        if not api_key:
            raise ValueError("Set SENTINEL_GATEWAY_API_KEY in backend configuration")
        if not math.isfinite(timeout_ms) or timeout_ms <= 0:
            raise ValueError("SENTINEL_GATEWAY_TIMEOUT_MS must be positive")
        self._api_key = api_key
        self.timeout = timeout_ms / 1000
        self.transport = transport

    @classmethod
    def from_settings(cls, settings):
        return cls(settings.SENTINEL_GATEWAY_URL, settings.SENTINEL_GATEWAY_API_KEY,
                   settings.SENTINEL_GATEWAY_TIMEOUT_MS)

    def _options(self):
        return dict(timeout=self.timeout, transport=self.transport, follow_redirects=False,
                    headers={"Authorization": f"Bearer {self._api_key}"})

    def _decode(self, response):
        request_id = response.headers.get("x-request-id")
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if not response.is_success:
            error = payload.get("error") if isinstance(payload, dict) else None
            error = error if isinstance(error, dict) else {}
            raise SentinelError(error.get("code") or "HTTP_ERROR", status=response.status_code,
                                request_id=request_id or error.get("request_id"),
                                retry_after=response.headers.get("retry-after"))
        if not isinstance(payload, dict):
            raise SentinelError("INVALID_JSON", request_id=request_id)
        metadata = {"request_id": _safe_diagnostic(request_id),
                    "selected_tier": _safe_diagnostic(response.headers.get("x-ai-gateway-selected-tier"))}
        log.info("Sentinel success status=%s request_id=%s tier=%s",
                 response.status_code, metadata["request_id"], metadata["selected_tier"])
        return payload, metadata

    def request(self, path, body=None):
        self._check_path(path, body)
        try:
            with httpx.Client(**self._options()) as client:
                response = client.request("GET" if body is None else "POST", self.url + path,
                                          **({} if body is None else {"json": body}))
            return self._decode(response)
        except httpx.TimeoutException:
            raise SentinelError("TIMEOUT") from None
        except httpx.RequestError:
            raise SentinelError("NETWORK_ERROR") from None

    async def arequest(self, path, body=None):
        self._check_path(path, body)
        try:
            async with httpx.AsyncClient(**self._options()) as client:
                response = await client.request("GET" if body is None else "POST", self.url + path,
                                                **({} if body is None else {"json": body}))
            return self._decode(response)
        except httpx.TimeoutException:
            raise SentinelError("TIMEOUT") from None
        except httpx.RequestError:
            raise SentinelError("NETWORK_ERROR") from None

    @staticmethod
    def _check_path(path, body):
        if (path == "/v1/models" and body is None) or (
            path in {"/v1/chat/completions", "/v1/embeddings"} and isinstance(body, dict)
        ):
            return
        raise ValueError("Unsupported Sentinel endpoint or method")


def text_messages(messages: list[BaseMessage]) -> list[dict]:
    """Render graph history as text, without transmitting tool-call protocol fields.

    Tool results are reference data, never system instructions. Full graph
    messages and tool IDs remain in the application's checkpoint unchanged.
    """
    result = []
    for message in messages:
        content = message.content
        if isinstance(content, list):
            if any(not isinstance(b, str) and not (isinstance(b, dict) and b.get("type") == "text")
                   for b in content):
                raise ValueError("Sentinel supports text messages only")
            content = "\n".join(b if isinstance(b, str) else b["text"] for b in content)
        if message.type == "tool":
            result.append({"role": "user", "content": "Reference data from an application tool:\n" + content})
        elif message.type in {"system", "human", "ai"}:
            if message.type == "ai" and getattr(message, "tool_calls", None) and not content:
                continue
            result.append({"role": {"system": "system", "human": "user", "ai": "assistant"}[message.type],
                           "content": content})
        else:
            raise ValueError("Unsupported Sentinel message role")
    if not 1 <= len(result) <= 128:
        raise ValueError("Sentinel requires 1 to 128 text messages; shorten conversation context")
    return result


class SentinelChatModel(BaseChatModel):
    client: Any = Field(exclude=True, repr=False)
    model: str = "company-auto"
    max_tokens: int = Field(default=4096, gt=0)
    disable_streaming: bool = True
    max_input_chars: int = Field(default=100000, gt=0)

    @property
    def _llm_type(self):
        return "sentinel-text"

    def _body(self, messages, stop, kwargs):
        if stop or kwargs:
            raise ValueError("Sentinel text mode does not accept tool or provider-specific fields")
        rendered = text_messages(messages)
        if sum(len(m["content"]) for m in rendered) > self.max_input_chars:
            raise SentinelError("CONTEXT_TOO_LARGE")
        return {"model": self.model, "messages": rendered,
                "max_tokens": self.max_tokens, "stream": False}

    def _result(self, payload, metadata):
        choices = payload.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices else None
        message = choice.get("message") if isinstance(choice, dict) else None
        if not isinstance(message, dict) or not isinstance(message.get("content"), str) or message.get("tool_calls"):
            raise SentinelError("INVALID_CHAT_RESPONSE", request_id=metadata["request_id"])
        metadata = {**metadata, "finish_reason": choice.get("finish_reason"),
                    "token_usage": payload.get("usage", {})}
        text = message["content"]
        if choice.get("finish_reason") == "length":
            text += "\n\n[The answer reached its output limit and may be incomplete.]"
        return ChatResult(generations=[ChatGeneration(message=AIMessage(
            content=text, response_metadata=metadata))])

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return self._result(*self.client.request("/v1/chat/completions", self._body(messages, stop, kwargs)))

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        return self._result(*await self.client.arequest("/v1/chat/completions", self._body(messages, stop, kwargs)))

    def with_structured_output(self, schema, *, include_raw=False, **kwargs):
        """Local Pydantic validation of plain JSON text; no response_format/tools."""
        if include_raw or kwargs or not hasattr(schema, "model_validate_json"):
            raise ValueError("Sentinel structured text requires a Pydantic schema")
        instruction = ("Return only one JSON object matching this schema, with no markdown: "
                       + json.dumps(schema.model_json_schema()))

        def prepare(value):
            messages = self._convert_input(value).to_messages()
            return [SystemMessage(content=instruction)] + messages

        def parse(message):
            if message.response_metadata.get("finish_reason") == "length":
                raise SentinelError("TRUNCATED_JSON", request_id=message.response_metadata.get("request_id"))
            try:
                return schema.model_validate_json(message.content)
            except ValueError:
                raise SentinelError("INVALID_STRUCTURED_RESPONSE",
                                    request_id=message.response_metadata.get("request_id")) from None

        return RunnableLambda(prepare) | self | RunnableLambda(parse)


class SentinelEmbeddings(Embeddings):
    def __init__(self, client, model, dimensions, batch_size=64):
        if not model or model in {"company-auto", "company-small", "company-medium", "company-high", "company-embedding"}:
            raise ValueError("Set SENTINEL_EMBEDDING_MODEL to an exact approved public model name")
        if not isinstance(dimensions, int) or isinstance(dimensions, bool) or dimensions < 1:
            raise ValueError("Set SENTINEL_EMBEDDING_DIMENSIONS to the approved vector size")
        if not 1 <= batch_size <= 2048:
            raise ValueError("Sentinel embedding batch size must be between 1 and 2048")
        self.client, self.model, self.dimensions, self.batch_size = client, model, dimensions, batch_size

    def _batches(self, texts):
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("Embedding inputs must be non-empty text")
        for start in range(0, len(texts), self.batch_size):
            yield {"model": self.model, "input": texts[start:start + self.batch_size], "encoding_format": "float"}

    def _vectors(self, result, count):
        payload, metadata = result
        items = payload.get("data")
        if not isinstance(items, list) or len(items) != count:
            raise SentinelError("INVALID_EMBEDDINGS", request_id=metadata["request_id"])
        ordered = [None] * count
        for item in items:
            index = item.get("index") if isinstance(item, dict) else None
            vector = item.get("embedding") if isinstance(item, dict) else None
            if (type(index) is not int or not 0 <= index < count or ordered[index] is not None
                or not isinstance(vector, list) or len(vector) != self.dimensions
                or any(type(v) not in {int, float} or not math.isfinite(v) for v in vector)):
                raise SentinelError("INVALID_EMBEDDINGS", request_id=metadata["request_id"])
            ordered[index] = vector
        return ordered

    def embed_documents(self, texts):
        vectors = []
        for body in self._batches(texts):
            vectors.extend(self._vectors(self.client.request("/v1/embeddings", body), len(body["input"])))
        return vectors

    async def aembed_documents(self, texts):
        vectors = []
        for body in self._batches(texts):
            vectors.extend(self._vectors(await self.client.arequest("/v1/embeddings", body), len(body["input"])))
        return vectors

    def embed_query(self, text):
        return self.embed_documents([text])[0]

    async def aembed_query(self, text):
        return (await self.aembed_documents([text]))[0]
