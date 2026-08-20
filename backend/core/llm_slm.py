"""
Factory for the internal SLM (Ollama) — used by the Ask HR SLM demo agent.
Kept separate from core/llm.py so the existing OpenAI/Gemini-backed agents
are untouched.
"""

import logging

from core.config import settings

log = logging.getLogger(__name__)


def get_slm_chat_model():
    """Return a chat model for the internal SLM server."""
    provider = (settings.SLM_PROVIDER or "ollama").lower().strip()

    if provider == "openai":
        from langchain_openai import ChatOpenAI

        # Many OpenAI-compatible gateways accept any non-empty API key.
        api_key = settings.SLM_API_KEY or settings.OPENAI_API_KEY or "dummy"
        log.info(
            f"Initialized SLM chat model (openai-compat): "
            f"{settings.SLM_MODEL} @ {settings.SLM_BASE_URL}"
        )
        return ChatOpenAI(
            model=settings.SLM_MODEL,
            api_key=api_key,
            base_url=settings.SLM_BASE_URL,
            temperature=0.7,
            max_tokens=1000,
        )

    from langchain_ollama import ChatOllama

    log.info(
        f"Initialized SLM chat model (ollama): "
        f"{settings.SLM_MODEL} @ {settings.SLM_BASE_URL}"
    )
    return ChatOllama(
        model=settings.SLM_MODEL,
        base_url=settings.SLM_BASE_URL,
        temperature=0.7,
        num_predict=1000,
    )


def get_slm_classifier_model():
    """Non-streaming SLM instance for intent classification.

    Streaming is disabled so its tokens never bubble up through the
    chat router's `on_chat_model_stream` events to the user.
    """
    provider = (settings.SLM_PROVIDER or "ollama").lower().strip()

    if provider == "openai":
        from langchain_openai import ChatOpenAI

        api_key = settings.SLM_API_KEY or settings.OPENAI_API_KEY or "dummy"
        return ChatOpenAI(
            model=settings.SLM_MODEL,
            api_key=api_key,
            base_url=settings.SLM_BASE_URL,
            temperature=0,
            max_tokens=80,
            streaming=False,
        )

    from langchain_ollama import ChatOllama

    return ChatOllama(
        model=settings.SLM_MODEL,
        base_url=settings.SLM_BASE_URL,
        temperature=0,
        num_predict=80,
        disable_streaming=True,
    )


def get_slm_embedding_model():
    """Return an embedding model for the SLM retrieval path."""
    provider = (settings.SLM_PROVIDER or "ollama").lower().strip()

    if provider == "openai":
        from langchain_openai import OpenAIEmbeddings

        api_key = settings.SLM_API_KEY or settings.OPENAI_API_KEY or "dummy"
        base_url = settings.SLM_EMBEDDING_BASE_URL or settings.SLM_BASE_URL
        log.info(
            f"Initialized SLM embedding model (openai-compat): "
            f"{settings.SLM_EMBEDDING_MODEL} @ {base_url}"
        )
        return OpenAIEmbeddings(
            model=settings.SLM_EMBEDDING_MODEL,
            api_key=api_key,
            base_url=base_url,
        )

    from langchain_ollama import OllamaEmbeddings

    log.info(
        f"Initialized SLM embedding model (ollama): {settings.SLM_EMBEDDING_MODEL} "
        f"@ {settings.SLM_EMBEDDING_BASE_URL}"
    )
    return OllamaEmbeddings(
        model=settings.SLM_EMBEDDING_MODEL,
        base_url=settings.SLM_EMBEDDING_BASE_URL,
    )
