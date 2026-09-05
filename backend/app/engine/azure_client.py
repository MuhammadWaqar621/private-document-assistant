"""
Thin wrapper around the Azure OpenAI SDK clients used by the RAG engine.

Configuration is read directly from environment variables (not from
app.core.config.Settings) so that app/engine/ has zero dependency on the
rest of the FastAPI application - see app/engine/__init__.py for the full
isolation contract. In practice these values still originate from the same
.env file (docker-compose's `env_file: .env` exports them as real process
environment variables for the backend container), so nothing needs to be
duplicated - this module just doesn't *import* the app's settings module.

Two separate Azure OpenAI deployments are used:
  - embeddings: AZURE_EM_ENDPOINT / AZURE_EM_API_KEY / AZURE_EM_API_VERSION
    / AZURE_EM_MODEL
  - chat (any Azure OpenAI chat deployment): LLM_ENDPOINT /
    LLM_ENDPOINT_APIKEY / LLM_MODEL_NAME

AZURE_EM_DIMENSIONS configures the embedding vector size used when creating
the Qdrant collection (see qdrant_client.py) - Azure text-embedding models
are typically 1536 (text-embedding-ada-002 / text-embedding-3-small) or
3072 (text-embedding-3-large) dimensions; default here is 1536.
"""

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

from openai import AsyncAzureOpenAI, AzureOpenAI

DEFAULT_EMBEDDING_DIMENSIONS = 1536
# Fallback chat api_version when the deployment doesn't set its own - Azure
# OpenAI chat completions don't need a version as recent as embeddings do,
# but the client requires *some* value. Override via
# LLM_ENDPOINT_API_VERSION if a specific deployment needs it.
DEFAULT_CHAT_API_VERSION = "2024-08-01-preview"


@dataclass(frozen=True)
class EmbeddingConfig:
    endpoint: str
    api_key: str
    api_version: str
    model: str


@dataclass(frozen=True)
class ChatConfig:
    endpoint: str
    api_key: str
    api_version: str
    model: str


def _clean(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    return value or None


def get_embedding_config() -> Optional[EmbeddingConfig]:
    endpoint = _clean(os.getenv("AZURE_EM_ENDPOINT"))
    api_key = _clean(os.getenv("AZURE_EM_API_KEY"))
    api_version = _clean(os.getenv("AZURE_EM_API_VERSION"))
    model = _clean(os.getenv("AZURE_EM_MODEL"))
    if not (endpoint and api_key and api_version and model):
        return None
    return EmbeddingConfig(endpoint=endpoint, api_key=api_key, api_version=api_version, model=model)


def get_chat_config() -> Optional[ChatConfig]:
    endpoint = _clean(os.getenv("LLM_ENDPOINT"))
    api_key = _clean(os.getenv("LLM_ENDPOINT_APIKEY"))
    model = _clean(os.getenv("LLM_MODEL_NAME"))
    if not (endpoint and api_key and model):
        return None
    api_version = (
        _clean(os.getenv("LLM_ENDPOINT_API_VERSION"))
        or _clean(os.getenv("AZURE_EM_API_VERSION"))
        or DEFAULT_CHAT_API_VERSION
    )
    return ChatConfig(endpoint=endpoint, api_key=api_key, api_version=api_version, model=model)


def azure_ai_configured() -> bool:
    """True only when both the embedding and chat deployments are fully
    configured - used by app/api/config_status.py's `azure_ai` group and by
    the documents/messages endpoints to fail fast with a 503."""
    return get_embedding_config() is not None and get_chat_config() is not None


def get_embedding_dimensions() -> int:
    """AZURE_EM_DIMENSIONS, defaulting to 1536. Kept independent of pydantic
    Settings deliberately (see module docstring); config_status.py does not
    require this var to report azure_ai as configured, since a sensible
    code default exists here."""
    raw = _clean(os.getenv("AZURE_EM_DIMENSIONS"))
    if not raw:
        return DEFAULT_EMBEDDING_DIMENSIONS
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_EMBEDDING_DIMENSIONS


@lru_cache
def get_embedding_client() -> AzureOpenAI:
    config = get_embedding_config()
    if config is None:
        raise RuntimeError("Azure OpenAI embeddings are not configured (AZURE_EM_* env vars).")
    return AzureOpenAI(
        azure_endpoint=config.endpoint,
        api_key=config.api_key,
        api_version=config.api_version,
    )


@lru_cache
def get_async_chat_client() -> AsyncAzureOpenAI:
    config = get_chat_config()
    if config is None:
        raise RuntimeError(
            "Azure OpenAI chat is not configured (LLM_ENDPOINT* env vars)."
        )
    return AsyncAzureOpenAI(
        azure_endpoint=config.endpoint,
        api_key=config.api_key,
        api_version=config.api_version,
    )
