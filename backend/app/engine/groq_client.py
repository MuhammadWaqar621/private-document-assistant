"""
Thin wrapper around the Groq API used for speech-to-text (Whisper),
text-to-speech (PlayAI TTS), and - when selected via LLM_PROVIDER (see
app/engine/llm_provider.py) - chat completions.

Configuration is read directly from environment variables (not from
app.core.config.Settings) so that app/engine/ has zero dependency on the
rest of the FastAPI application - see app/engine/__init__.py for the full
isolation contract and app/engine/azure_client.py's module docstring for
the same reasoning applied to Azure OpenAI. In practice these values still
originate from the same .env file (docker-compose's `env_file: .env`
exports them as real process environment variables for the backend
container).

Groq exposes an OpenAI-compatible API, so the `openai` package (already a
dependency, used for Azure OpenAI elsewhere in this engine) is reused here
too, just pointed at Groq's base URL - no separate SDK is needed.

Env vars:
  - GROQ_API_KEY: required for anything in this module to work.
  - GROQ_STT_MODEL: Whisper model for transcribe_audio(). Default below.
  - GROQ_TTS_MODEL / GROQ_TTS_VOICE: model/voice for synthesize_speech().
    Defaults below.
  - GROQ_LLM_MODEL: chat-completion model used when LLM_PROVIDER="groq"
    (the default - see app/engine/llm_provider.py). Default below.
"""

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

from openai import AsyncOpenAI, OpenAI

GROQ_BASE_URL = "https://api.groq.com/openai/v1"

DEFAULT_STT_MODEL = "whisper-large-v3"
DEFAULT_TTS_MODEL = "playai-tts"
DEFAULT_TTS_VOICE = "Fritz-PlayAI"
DEFAULT_CHAT_MODEL = "llama-3.3-70b-versatile"

# Hard cap on how much text synthesize_speech() will send to Groq per call -
# bounds cost/latency for a very long assistant reply. Callers may pass
# longer text; it is simply truncated here rather than rejected.
_TTS_MAX_CHARS = 2000


@dataclass(frozen=True)
class GroqChatConfig:
    model: str


def _clean(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _api_key() -> Optional[str]:
    return _clean(os.getenv("GROQ_API_KEY"))


def groq_configured() -> bool:
    """True iff GROQ_API_KEY is set to a non-empty value."""
    return _api_key() is not None


def get_groq_chat_config() -> Optional[GroqChatConfig]:
    """GROQ_LLM_MODEL, defaulting to DEFAULT_CHAT_MODEL - None (not
    configured) when GROQ_API_KEY itself is unset, same shape as
    azure_client.get_chat_config()."""
    if not groq_configured():
        return None
    model = _clean(os.getenv("GROQ_LLM_MODEL")) or DEFAULT_CHAT_MODEL
    return GroqChatConfig(model=model)


@lru_cache
def get_groq_client() -> OpenAI:
    """Synchronous client - used for the (blocking, one-shot) STT/TTS
    calls below, mirroring azure_client.get_embedding_client()."""
    api_key = _api_key()
    if api_key is None:
        raise RuntimeError("Groq is not configured (GROQ_API_KEY env var).")
    return OpenAI(api_key=api_key, base_url=GROQ_BASE_URL)


@lru_cache
def get_async_groq_chat_client() -> AsyncOpenAI:
    """Async client - used for streaming chat completions when
    LLM_PROVIDER="groq" (see app/engine/llm_provider.py), mirroring
    azure_client.get_async_chat_client()."""
    api_key = _api_key()
    if api_key is None:
        raise RuntimeError("Groq is not configured (GROQ_API_KEY env var).")
    return AsyncOpenAI(api_key=api_key, base_url=GROQ_BASE_URL)


def transcribe_audio(audio_bytes: bytes, filename: str) -> str:
    """Speech-to-text via Groq's Whisper endpoint. Raises on any API
    failure - callers (app/api/speech.py) translate that into a clear HTTP
    error rather than a raw 500, matching app/core/email.py's pattern for
    external-service failures."""
    client = get_groq_client()
    model = _clean(os.getenv("GROQ_STT_MODEL")) or DEFAULT_STT_MODEL
    response = client.audio.transcriptions.create(model=model, file=(filename, audio_bytes))
    return response.text


def synthesize_speech(text: str) -> bytes:
    """Text-to-speech via Groq's PlayAI TTS endpoint, returning raw MP3
    bytes (response_format="mp3"). `text` is capped at _TTS_MAX_CHARS
    characters before being sent, to bound cost/latency on a very long
    assistant reply - see module docstring."""
    client = get_groq_client()
    model = _clean(os.getenv("GROQ_TTS_MODEL")) or DEFAULT_TTS_MODEL
    voice = _clean(os.getenv("GROQ_TTS_VOICE")) or DEFAULT_TTS_VOICE
    response = client.audio.speech.create(
        model=model,
        voice=voice,
        input=text[:_TTS_MAX_CHARS],
        response_format="mp3",
    )
    return response.read()
