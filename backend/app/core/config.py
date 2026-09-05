"""
Application configuration.

Settings are loaded from environment variables (and a local .env file when
present) via pydantic-settings. Nothing here should hold secrets directly -
values are supplied through the environment at runtime (see .env.example
at the repo root for the full list of variables).
"""

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central application settings.

    Grouped roughly by concern. Auth-related settings (JWT, Google OAuth,
    SMTP) back the endpoints in app/api/auth.py - each optional group is
    considered "configured" only once every variable in it is set (see
    app/api/config_status.py), and the endpoints that depend on an
    unconfigured group return a clear 503 rather than crashing.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- App metadata -----------------------------------------------------
    APP_NAME: str = "querynest"
    ENVIRONMENT: str = "development"

    # --- Database -----------------------------------------------------
    DATABASE_URL: str = "postgresql://postgres:postgres@postgres:5432/querynest"

    # --- Qdrant (vector DB) ------------------------------------------------
    QDRANT_URL: str = "http://qdrant:6333"

    # --- Frontend (used to build links in emails / OAuth redirects) --------
    FRONTEND_URL: str = "http://localhost:5173"

    # --- Azure OpenAI - Embeddings ------------------------------------------
    AZURE_EM_ENDPOINT: Optional[str] = None
    AZURE_EM_API_KEY: Optional[str] = None
    AZURE_EM_API_VERSION: Optional[str] = None
    AZURE_EM_MODEL: Optional[str] = None

    # --- Azure OpenAI - Chat (mini model) -----------------------------------
    LLM_ENDPOINT_MINI_MODEL: Optional[str] = None
    LLM_ENDPOINT_MINI_MODEL_APIKEY: Optional[str] = None
    MINI_MODEL_NAME: Optional[str] = None

    # --- JWT (auth) ----------------------------------------------------------
    JWT_SECRET_KEY: Optional[str] = None
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # --- Google OAuth ("Sign in with Google") ---------------------------------
    GOOGLE_CLIENT_ID: Optional[str] = None
    GOOGLE_CLIENT_SECRET: Optional[str] = None

    # --- SMTP (forgot-password emails) ---------------------------------------
    SMTP_HOST: Optional[str] = None
    SMTP_PORT: int = 587
    SMTP_USERNAME: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    SMTP_FROM_EMAIL: Optional[str] = None


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (env is only parsed once)."""
    return Settings()
