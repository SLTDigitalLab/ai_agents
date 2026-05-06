"""
Factory for the internal SLM (Ollama) — used by the Ask HR SLM demo agent.
Kept separate from core/llm.py so the existing OpenAI/Gemini-backed agents
are untouched.
"""

import logging

from core.config import settings

log = logging.getLogger(__name__)


def get_slm_chat_model():
    """Return a ChatOllama instance pointed at the internal SLM server."""
    from langchain_ollama import ChatOllama

    log.info(
        f"Initialized SLM chat model: {settings.SLM_MODEL} @ {settings.SLM_BASE_URL}"
    )
    return ChatOllama(
        model=settings.SLM_MODEL,
        base_url=settings.SLM_BASE_URL,
        temperature=0.7,
        num_predict=1000,
    )


def get_slm_embedding_model():
    """Return an OllamaEmbeddings instance for the SLM embedding model."""
    from langchain_ollama import OllamaEmbeddings

    log.info(
        f"Initialized SLM embedding model: {settings.SLM_EMBEDDING_MODEL} "
        f"@ {settings.SLM_EMBEDDING_BASE_URL}"
    )
    return OllamaEmbeddings(
        model=settings.SLM_EMBEDDING_MODEL,
        base_url=settings.SLM_EMBEDDING_BASE_URL,
    )
