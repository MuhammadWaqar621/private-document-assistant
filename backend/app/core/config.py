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

    Grouped roughly by concern. Auth-related settings (JWT, SMTP) back the
    endpoints in app/api/auth.py - each optional group is
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
    APP_NAME: str = "QueryNest"
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
    # Embedding vector size, used to size the Qdrant collection (see
    # app/engine/qdrant_client.py). Not required for the `azure_ai`
    # config-status group - it has a sensible code default (1536) in
    # app/engine/azure_client.get_embedding_dimensions(), so a deployment
    # that doesn't set it is still considered fully configured. Declared
    # here too (even though app/engine/ reads env vars directly, not this
    # Settings object - see app/engine/azure_client.py's docstring) purely
    # so it shows up alongside the other AZURE_EM_* vars for anyone
    # inspecting Settings.
    AZURE_EM_DIMENSIONS: str = "1536"

    # --- Azure OpenAI - Chat (mini model) -----------------------------------
    LLM_ENDPOINT: Optional[str] = None
    LLM_ENDPOINT_APIKEY: Optional[str] = None
    LLM_MODEL_NAME: Optional[str] = None

    # --- Chat provider selection ---------------------------------------
    # "groq" (default) or "azure" - see app/engine/llm_provider.py. Only
    # the CHAT half of the RAG pipeline is selectable; embeddings above are
    # always Azure OpenAI (Groq has no embeddings API). Declared here
    # (even though app/engine/ reads env vars directly, not this Settings
    # object) purely so it shows up for anyone inspecting Settings, same as
    # AZURE_EM_DIMENSIONS above.
    LLM_PROVIDER: str = "groq"

    # --- Groq (speech-to-text, text-to-speech, and optionally chat) --------
    GROQ_API_KEY: Optional[str] = None
    # The model/voice vars below all have sensible code defaults in
    # app/engine/groq_client.py, so - like AZURE_EM_DIMENSIONS - they are
    # not required for any config-status group to report as configured.
    GROQ_STT_MODEL: str = "whisper-large-v3"
    GROQ_TTS_MODEL: str = "playai-tts"
    GROQ_TTS_VOICE: str = "Fritz-PlayAI"
    GROQ_LLM_MODEL: str = "llama-3.3-70b-versatile"

    # --- JWT (auth) ----------------------------------------------------------
    JWT_SECRET_KEY: Optional[str] = None
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7

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
