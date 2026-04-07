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

    # Guardrail Classifier (cheap/fast model for input safety)
    GUARDRAIL_PROVIDER: str = "openai"
    GUARDRAIL_MODEL: str = "gpt-4.1-nano"
    GUARDRAIL_API_KEY: Optional[str] = None  # falls back to provider key

    QDRANT_URL: str
    POSTGRES_URL: str

    # Optional: unset disables Redis response caching (local dev without Redis).
    REDIS_URL: Optional[str] = None

    # Bitrix24 CRM
    BITRIX24_WEBHOOK_URL: str = ""

    # Email / SMTP (used by fastapi-mail)
    MAIL_USERNAME: Optional[str] = None
    MAIL_PASSWORD: Optional[str] = None
    MAIL_FROM: Optional[str] = None
    MAIL_PORT: int = 587
    MAIL_SERVER: str = "smtp.gmail.com"
    MAIL_STARTTLS: bool = True
    MAIL_SSL_TLS: bool = False

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

