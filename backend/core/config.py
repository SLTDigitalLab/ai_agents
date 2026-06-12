import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from pydantic_settings import BaseSettings
from fastapi_mail import ConnectionConfig

# backend/core/config.py → backend/core → backend → project root
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(ROOT_DIR / ".env")


class Settings(BaseSettings):
    GOOGLE_API_KEY: Optional[str] = None
    OPENAI_API_KEY: Optional[str] = None
    
    # LLM and Embedding Configuration
    LLM_PROVIDER: str = "gemini" # 'gemini', 'openai'
    LLM_MODEL: str = "gemini-3-flash-preview"
    LLM_API_KEY: Optional[str] = None
    LLM_BASE_URL: Optional[str] = None
    
    EMBEDDING_PROVIDER: str = "gemini" # 'gemini', 'openai'
    EMBEDDING_MODEL: str = "models/gemini-embedding-001"
    EMBEDDING_DIMENSIONS: int = 3072
    EMBEDDING_API_KEY: Optional[str] = None
    EMBEDDING_BASE_URL: Optional[str] = None

    # Routing-only embedding model — used ONLY by the supervisor to score query
    # similarity against specialist profiles. Independent of the main embedding
    # model so Qdrant collections stay valid. Defaults to a small/fast OpenAI
    # model since routing only needs cosine similarity on short text.
    ROUTING_EMBEDDING_PROVIDER: str = "openai"
    ROUTING_EMBEDDING_MODEL: str = "text-embedding-3-small"
    ROUTING_EMBEDDING_API_KEY: Optional[str] = None

    # Guardrail Classifier (cheap/fast model for input safety)
    GUARDRAIL_PROVIDER: str = "openai"
    GUARDRAIL_MODEL: str = "gpt-4.1-nano"
    GUARDRAIL_API_KEY: Optional[str] = None  # falls back to provider key

    # Internal SLM (Ollama) — used by the Ask HR SLM demo agent only.
    SLM_BASE_URL: str = "http://localhost:11434"
    SLM_EMBEDDING_BASE_URL: str = "http://localhost:11434"
    SLM_MODEL: str = "deepseek-r1:1.5b"
    SLM_EMBEDDING_MODEL: str = "nomic-embed-text"
    SLM_EMBEDDING_DIMENSIONS: int = 768

    QDRANT_URL: str
    POSTGRES_URL: str

    # API key for external clients (e.g. voice assistant) hitting the Finance retrieval endpoint
    VOICE_ASSISTANT_API_KEY: Optional[str] = None

    # API key for the generic /api/v1/kb/{agent_id}/retrieve endpoint used by
    # local developer instances so they can query prod-embedded vectors instead
    # of re-embedding files locally. Rotated independently of the voice key.
    DEV_KB_API_KEY: Optional[str] = None

    # Comma-separated agent_ids that are exposed via /api/v1/kb/{agent_id}/retrieve.
    # Anything not in this list returns 404 even with a valid API key.
    KB_RETRIEVAL_ALLOWLIST: str = ""

    # When set, search_knowledge_base proxies retrieval to a remote Ask SLT
    # instance (typically prod) instead of querying a local Qdrant. Devs use
    # this to avoid running ingestion on every checkout.
    KB_REMOTE_URL: Optional[str] = None
    KB_REMOTE_API_KEY: Optional[str] = None

    # Bitrix24 CRM
    BITRIX24_WEBHOOK_URL: str = ""

    # JSON mapping of admin email -> list of allowed agent_ids for ingestion.
    # Use ["*"] to grant access to all agents. Example:
    #   ADMIN_AGENT_MAP={"hr.admin@slt.com.lk":["hr"],"super@slt.com.lk":["*"]}
    ADMIN_AGENT_MAP: str = "{}"

    # Email / SMTP (used by fastapi-mail)
    MAIL_USERNAME: Optional[str] = None
    MAIL_PASSWORD: Optional[str] = None
    MAIL_FROM: Optional[str] = None
    MAIL_PORT: int = 587
    MAIL_SERVER: str = "smtp.gmail.com"
    MAIL_STARTTLS: bool = True
    MAIL_SSL_TLS: bool = False

    NEO4J_URI: str | None = None
    NEO4J_USERNAME: str | None = None
    NEO4J_PASSWORD: str | None = None
    NEO4J_DATABASE: str = "neo4j"

    LIFESTORE_QDRANT_COLLECTION: str = "lifestore_docs"

    N8N_HELPDESK_WEBHOOK_URL: Optional[str] = None 


    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()


def get_mail_config() -> ConnectionConfig:
    """Return a fastapi-mail ConnectionConfig built from env vars."""
    return ConnectionConfig(
        MAIL_USERNAME=os.getenv("MAIL_USERNAME"),
        MAIL_PASSWORD=os.getenv("MAIL_PASSWORD"),
        MAIL_FROM=os.getenv("MAIL_FROM"),
        MAIL_PORT=int(os.getenv("MAIL_PORT", "587")),
        MAIL_SERVER=os.getenv("MAIL_SERVER", "smtp.gmail.com"),
        MAIL_STARTTLS=os.getenv("MAIL_STARTTLS", "True").lower() in ("true", "1", "t"),
        MAIL_SSL_TLS=os.getenv("MAIL_SSL_TLS", "False").lower() in ("true", "1", "t"),
        USE_CREDENTIALS=True,
        VALIDATE_CERTS=True,
    )

