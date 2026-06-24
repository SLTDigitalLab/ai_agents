"""
Factory for initializing LLMs and Embeddings based on environment configurations.
"""

import logging
from functools import lru_cache
from typing import Optional

from core.config import settings

log = logging.getLogger(__name__)


# gemini-embedding-2 ignores the `task_type` field; the task must instead be
# given as a text-instruction prefix (Vertex docs). These are pure prefixes
# (content is appended), so chunks containing '{' or '}' are safe — we do NOT
# use str.format. The prefix only steers the embedding vector; langchain_qdrant
# stores the original chunk text as the payload, so the text the LLM reads is
# unchanged.
#
# Asymmetric retrieval (KB search): document side vs query side differ.
_VERTEX_DOC_PREFIX = "title: none | text: "
_VERTEX_QUERY_PREFIX = "task: search result | query: "
# Symmetric similarity (supervisor routing): same prefix on both sides.
_VERTEX_SIMILARITY_PREFIX = "task: sentence similarity | query: "


def _make_vertex_embeddings(model_name: str, *, doc_prefix: str, query_prefix: str):
    """Build a Vertex AI embedding model wrapper for gemini-embedding-*.

    Two gemini-embedding-specific behaviors are handled here:

    1. Single-input batches. These models accept only ONE input text per
       request (unlike text-embedding-004/005). The langchain-google-genai
       default batch size is 100, so a multi-text batch comes back as a single
       embedding and hybrid ingestion fails with "Mismatched length between
       dense and sparse embeddings". We force batch_size=1.

    2. Task instructions. gemini-embedding-2 ignores `task_type`; the task is
       encoded as a text prefix instead. We prepend `doc_prefix` to documents
       and `query_prefix` to queries.
    """
    from langchain_google_genai import GoogleGenerativeAIEmbeddings

    class _VertexEmbeddings(GoogleGenerativeAIEmbeddings):
        def embed_documents(self, texts, **kwargs):
            kwargs.setdefault("batch_size", 1)
            return super().embed_documents([doc_prefix + t for t in texts], **kwargs)

        def embed_query(self, text, **kwargs):
            return super().embed_query(query_prefix + text, **kwargs)

    return _VertexEmbeddings(
        model=model_name,
        vertexai=True,
        project=settings.VERTEX_PROJECT_ID,
        location=settings.VERTEX_LOCATION,
    )


@lru_cache(maxsize=1)
def get_chat_model():
    """
    Returns an instantiated chat model based on the LLM_PROVIDER setting.
    """
    provider = settings.LLM_PROVIDER.lower().strip()
    model_name = settings.LLM_MODEL
    api_key = settings.LLM_API_KEY
    base_url = settings.LLM_BASE_URL

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
    elif provider == "vertex":
        # Vertex AI via the unified google-genai SDK (langchain-google-genai),
        # NOT langchain-google-vertexai (whose ChatVertexAI is deprecated).
        # Auth is the service account in GOOGLE_APPLICATION_CREDENTIALS.
        from langchain_google_genai import ChatGoogleGenerativeAI

        log.info(
            f"Initialized Vertex AI chat model: {model_name} "
            f"(project={settings.VERTEX_PROJECT_ID}, location={settings.VERTEX_LOCATION})"
        )
        return ChatGoogleGenerativeAI(
            model=model_name,
            vertexai=True,
            project=settings.VERTEX_PROJECT_ID,
            location=settings.VERTEX_LOCATION,
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
    elif provider == "vertex":
        # Vertex AI via the unified google-genai SDK (langchain-google-genai),
        # NOT langchain-google-vertexai (whose VertexAIEmbeddings is deprecated).
        # Asymmetric retrieval format (KB search); batch_size=1 (single-input).
        log.info(
            f"Initialized Vertex AI embedding model: {model_name} "
            f"(project={settings.VERTEX_PROJECT_ID}, location={settings.VERTEX_LOCATION})"
        )
        return _make_vertex_embeddings(
            model_name,
            doc_prefix=_VERTEX_DOC_PREFIX,
            query_prefix=_VERTEX_QUERY_PREFIX,
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
    elif provider == "vertex":
        # Symmetric similarity format (routing is profile/query similarity).
        # NOTE: 'task: classification' was measured and did NOT separate
        # departments better (it pushed finance to 4th on a tenders query), so
        # we keep sentence-similarity. The weak separation is driven by overly
        # broad profile texts, not the task prefix.
        log.info(f"Initialized routing embedding model (Vertex AI): {model_name}")
        return _make_vertex_embeddings(
            model_name,
            doc_prefix=_VERTEX_SIMILARITY_PREFIX,
            query_prefix=_VERTEX_SIMILARITY_PREFIX,
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
    elif provider == "vertex":
        from langchain_google_genai import ChatGoogleGenerativeAI

        log.info(f"Initialized guardrail model (Vertex AI): {model_name}")
        return ChatGoogleGenerativeAI(
            model=model_name,
            vertexai=True,
            project=settings.VERTEX_PROJECT_ID,
            location=settings.VERTEX_LOCATION,
            temperature=0,
        )
    else:
        raise ValueError(f"Unsupported GUARDRAIL_PROVIDER: {provider}")
