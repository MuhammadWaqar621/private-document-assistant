"""Unit/integration tests for GET /api/config/status - specifically the
`rag` group's provider-aware logic (see app/api/config_status.py's module
docstring): embeddings are always Azure, but the required chat vars depend
on which provider LLM_PROVIDER selects. Also covers the `speech` group and
the `llm_provider` field.

These monkeypatch real env vars + clear the Settings cache (same pattern
as test_auth_api.py's test_config_status_reflects_unset_groups), not the
config_status module's functions - the whole point here is proving the
env-var-driven logic itself, not just that some function got called."""

from app.core.config import get_settings

_AZURE_EM_VARS = {
    "AZURE_EM_ENDPOINT": "https://example.openai.azure.com",
    "AZURE_EM_API_KEY": "fake-key",
    "AZURE_EM_API_VERSION": "2024-08-01-preview",
    "AZURE_EM_MODEL": "text-embedding-3-small",
}
_AZURE_CHAT_VARS = {
    "LLM_ENDPOINT": "https://example.openai.azure.com",
    "LLM_ENDPOINT_APIKEY": "fake-key",
    "LLM_MODEL_NAME": "gpt-4o-mini",
}


def _set(monkeypatch, **env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


def test_rag_false_when_embeddings_missing_even_if_groq_key_set(client, monkeypatch):
    monkeypatch.delenv("AZURE_EM_ENDPOINT", raising=False)
    _set(monkeypatch, LLM_PROVIDER="groq", GROQ_API_KEY="fake-groq-key")

    body = client.get("/api/config/status").json()

    assert body["rag"] is False


def test_rag_true_with_embeddings_and_groq_key_when_provider_is_groq(client, monkeypatch):
    _set(monkeypatch, **_AZURE_EM_VARS, LLM_PROVIDER="groq", GROQ_API_KEY="fake-groq-key")
    monkeypatch.delenv("LLM_ENDPOINT", raising=False)  # azure chat vars deliberately unset

    body = client.get("/api/config/status").json()

    assert body["rag"] is True
    assert body["llm_provider"] == "groq"


def test_rag_false_when_provider_is_groq_but_groq_key_missing(client, monkeypatch):
    _set(monkeypatch, **_AZURE_EM_VARS, LLM_PROVIDER="groq")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    body = client.get("/api/config/status").json()

    assert body["rag"] is False


def test_rag_true_with_embeddings_and_azure_chat_when_provider_is_azure(client, monkeypatch):
    _set(monkeypatch, **_AZURE_EM_VARS, **_AZURE_CHAT_VARS, LLM_PROVIDER="azure")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)  # groq key deliberately unset

    body = client.get("/api/config/status").json()

    assert body["rag"] is True
    assert body["llm_provider"] == "azure"


def test_rag_false_when_provider_is_azure_but_azure_chat_vars_missing(client, monkeypatch):
    _set(monkeypatch, **_AZURE_EM_VARS, LLM_PROVIDER="azure", GROQ_API_KEY="fake-groq-key")
    monkeypatch.delenv("LLM_ENDPOINT", raising=False)

    body = client.get("/api/config/status").json()

    # Groq being configured must NOT count when the selected provider is
    # azure - only that provider's own vars matter.
    assert body["rag"] is False


def test_llm_provider_defaults_to_groq_when_unset(client, monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    get_settings.cache_clear()

    body = client.get("/api/config/status").json()

    assert body["llm_provider"] == "groq"


def test_speech_group_reflects_groq_api_key(client, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    get_settings.cache_clear()
    assert client.get("/api/config/status").json()["speech"] is False

    monkeypatch.setenv("GROQ_API_KEY", "fake-groq-key")
    get_settings.cache_clear()
    assert client.get("/api/config/status").json()["speech"] is True
