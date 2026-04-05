"""
Factory for initializing LLMs and Embeddings based on environment configurations.
"""

import logging
from typing import Optional

from core.config import settings

log = logging.getLogger(__name__)


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
    else:
        raise ValueError(f"Unsupported LLM_PROVIDER: {provider}")


def get_embedding_model():
    """
    Returns an instantiated embedding model based on the EMBEDDING_PROVIDER setting.
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
    else:
        raise ValueError(f"Unsupported EMBEDDING_PROVIDER: {provider}")


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
    else:
        raise ValueError(f"Unsupported GUARDRAIL_PROVIDER: {provider}")
