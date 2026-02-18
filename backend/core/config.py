from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    GOOGLE_API_KEY: str
    QDRANT_URL: str
    POSTGRES_URL: str

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
