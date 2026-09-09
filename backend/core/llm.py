"""
Factory for initializing LLMs and Embeddings based on environment configurations.
"""

import logging
import re
from functools import lru_cache
from typing import Optional

from core.config import settings

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_chat_model():
    """
    Returns an instantiated chat model based on the LLM_PROVIDER setting.
    """
    provider = settings.LLM_PROVIDER.lower().strip()
    model_name = settings.LLM_MODEL
    api_key = settings.LLM_API_KEY
    base_url = settings.LLM_BASE_URL

    if provider == "sentinel":
        return _sentinel_chat(settings.SENTINEL_GATEWAY_MODEL)

    if provider == "openai":
        from langchain_openai import ChatOpenAI
        
        # Fall back to global OPENAI_API_KEY if specific key is not set
        final_api_key = api_key or settings.OPENAI_API_KEY
        
        log.info(f"Initialized OpenAI chat model: {model_name} (Base URL: {base_url})")
        return ChatOpenAI(
            model=model_name,
            api_key=final_api_key,
            base_url=base_url,
            temperature=0,
        )
    elif provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI
        
        # Fall back to global GOOGLE_API_KEY if specific key is not set
        final_api_key = api_key or settings.GOOGLE_API_KEY
        
        log.info(f"Initialized Gemini chat model: {model_name}")
        return ChatGoogleGenerativeAI(
            model=model_name,
            google_api_key=final_api_key,
            streaming=True,
            temperature=0,
        )
    else:
        raise ValueError(f"Unsupported LLM_PROVIDER: {provider}")


@lru_cache(maxsize=1)
def get_embedding_model():
    """
    Returns an instantiated embedding model based on the EMBEDDING_PROVIDER setting.
    Used for document embedding (Qdrant ingestion + retrieval).
    """
    provider = settings.EMBEDDING_PROVIDER.lower().strip()
    model_name = settings.EMBEDDING_MODEL
    api_key = settings.EMBEDDING_API_KEY
    base_url = settings.EMBEDDING_BASE_URL

    if provider == "sentinel":
        from core.vector_config import validate_sentinel_vector_config
        validate_sentinel_vector_config()
        return _sentinel_embeddings()

    if provider == "openai":
        from langchain_openai import OpenAIEmbeddings
        
        # Fall back to global OPENAI_API_KEY if specific key is not set
        final_api_key = api_key or settings.OPENAI_API_KEY
        
        # Determine dimension based on known openai embedding models if needed
        # By default openai gives varying dimensions 
        log.info(f"Initialized OpenAI embedding model: {model_name} (Base URL: {base_url})")
        return OpenAIEmbeddings(
            model=model_name,
            api_key=final_api_key,
            base_url=base_url,
        )
    elif provider == "gemini":
        from langchain_google_genai import GoogleGenerativeAIEmbeddings
        
        # Fall back to global GOOGLE_API_KEY if specific key is not set
        final_api_key = api_key or settings.GOOGLE_API_KEY
        
        log.info(f"Initialized Gemini embedding model: {model_name}")
        return GoogleGenerativeAIEmbeddings(
            model=model_name,
            google_api_key=final_api_key,
        )
    else:
        raise ValueError(f"Unsupported EMBEDDING_PROVIDER: {provider}")


@lru_cache(maxsize=1)
def get_routing_embedding_model():
    """
    Returns a smaller, faster embedding model used ONLY for supervisor routing
    similarity scoring. Independent from the main EMBEDDING_MODEL so Qdrant
    collections (embedded with the main model) keep working unchanged.

    Defaults to text-embedding-3-small (1536 dims, ~5x cheaper and 2-3x faster
    than text-embedding-3-large). Override via ROUTING_EMBEDDING_* env vars.
    """
    provider = settings.ROUTING_EMBEDDING_PROVIDER.lower().strip()
    model_name = settings.ROUTING_EMBEDDING_MODEL
    api_key = settings.ROUTING_EMBEDDING_API_KEY

    if provider == "sentinel":
        # Routing profiles and queries share this exact configuration. Profiles
        # are cached in memory and rebuilt after the backend restarts.
        return _sentinel_embeddings()

    if provider == "openai":
        from langchain_openai import OpenAIEmbeddings

        final_api_key = api_key or settings.OPENAI_API_KEY

        log.info(f"Initialized routing embedding model (OpenAI): {model_name}")
        return OpenAIEmbeddings(
            model=model_name,
            api_key=final_api_key,
        )
    elif provider == "gemini":
        from langchain_google_genai import GoogleGenerativeAIEmbeddings

        final_api_key = api_key or settings.GOOGLE_API_KEY

        log.info(f"Initialized routing embedding model (Gemini): {model_name}")
        return GoogleGenerativeAIEmbeddings(
            model=model_name,
            google_api_key=final_api_key,
        )
    else:
        raise ValueError(f"Unsupported ROUTING_EMBEDDING_PROVIDER: {provider}")


@lru_cache(maxsize=1)
def get_guardrail_model():
    """
    Returns a lightweight, fast LLM for the guardrail intent classifier.
    Uses GUARDRAIL_PROVIDER / GUARDRAIL_MODEL from settings.
    Defaults to gpt-4.1-nano (OpenAI) — cheap and fast for classification.
    """
    provider = settings.GUARDRAIL_PROVIDER.lower().strip()
    model_name = settings.GUARDRAIL_MODEL
    api_key = settings.GUARDRAIL_API_KEY

    if provider == "sentinel":
        return _sentinel_chat(settings.SENTINEL_GUARDRAIL_MODEL, max_tokens=300)

    if provider == "openai":
        from langchain_openai import ChatOpenAI

        final_api_key = api_key or settings.OPENAI_API_KEY

        log.info(f"Initialized guardrail model (OpenAI): {model_name}")
        return ChatOpenAI(
            model=model_name,
            api_key=final_api_key,
            temperature=0,
        )
    elif provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        final_api_key = api_key or settings.GOOGLE_API_KEY

        log.info(f"Initialized guardrail model (Gemini): {model_name}")
        return ChatGoogleGenerativeAI(
            model=model_name,
            google_api_key=final_api_key,
            temperature=0,
        )
    else:
        raise ValueError(f"Unsupported GUARDRAIL_PROVIDER: {provider}")


def _sentinel_chat(model, max_tokens=None):
    from core.sentinel import SentinelChatModel, SentinelClient
    return SentinelChatModel(
        client=SentinelClient.from_settings(settings), model=model,
        max_tokens=max_tokens or settings.SENTINEL_GATEWAY_MAX_TOKENS,
    )


def _sentinel_embeddings():
    from core.sentinel import SentinelClient, SentinelEmbeddings
    return SentinelEmbeddings(
        SentinelClient.from_settings(settings), settings.SENTINEL_EMBEDDING_MODEL,
        settings.SENTINEL_EMBEDDING_DIMENSIONS, settings.SENTINEL_EMBEDDING_BATCH_SIZE,
    )


def get_ingestion_embedding_model():
    if settings.SENTINEL_STAGE_EMBEDDINGS:
        from core.vector_config import cloud_collection_name
        cloud_collection_name("validation_docs", for_ingestion=True)
        return _sentinel_embeddings()
    return get_embedding_model()


@lru_cache(maxsize=1)
def get_tool_chat_model():
    """Retain native tool decisions in Sentinel's documented text-only mode."""
    if settings.LLM_PROVIDER.lower().strip() != "sentinel":
        return get_chat_model()
    if settings.SENTINEL_TOOL_PROVIDER != "openai":
        raise ValueError("Sentinel hybrid mode currently requires SENTINEL_TOOL_PROVIDER=openai")
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        model=settings.LLM_MODEL,
        api_key=settings.LLM_API_KEY or settings.OPENAI_API_KEY,
        base_url=settings.LLM_BASE_URL,
        disable_streaming=True,
        max_retries=0,
        timeout=settings.SENTINEL_GATEWAY_TIMEOUT_MS / 1000,
        tags=["sentinel_tool_planner"],
    )


async def invoke_agent_model(tool_model, messages):
    """Keep the tool loop; use Sentinel to write the final grounded answer.

    The planner's text draft is discarded; it is not streamed or checkpointed.
    Native tool calls are returned intact so existing ToolNodes execute them.
    """
    response = await tool_model.ainvoke(messages)
    if settings.LLM_PROVIDER.lower().strip() != "sentinel" or response.tool_calls:
        return response
    from langchain_core.messages import HumanMessage
    final = await get_chat_model().ainvoke(messages + [HumanMessage(content=(
        "The application's tool phase is complete. Write the final answer to the user's question "
        "using the reference data above and this application draft. Do not request further tool calls "
        "or invent facts. Preserve citations and any [RENDER_...] markers exactly. "
        "If no supporting information is available, explain that limitation.\n\nApplication draft:\n"
        + (response.content if isinstance(response.content, str) else str(response.content))
    ))])
    # Generating prose must not invent/change a checkout ID or open an
    # unrequested form. Keep the planner's exact UI markers, in final position.
    marker_pattern = r"\[RENDER_[A-Z0-9_]+(?::[^\]\r\n]+)?\]"
    draft = response.content if isinstance(response.content, str) else ""
    markers = list(dict.fromkeys(re.findall(marker_pattern, draft)))
    content = re.sub(marker_pattern, "", final.content).rstrip()
    if markers:
        content += "\n\n" + "\n".join(markers)
    return final.model_copy(update={"content": content})
