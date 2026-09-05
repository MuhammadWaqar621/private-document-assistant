"""Unit tests for engine/llm_provider.py - the LLM_PROVIDER selection layer
that picks between Groq (default) and Azure OpenAI for chat completions
(embeddings are always Azure, unaffected by this).

Real API clients are never constructed here: azure_client.get_chat_config/
get_async_chat_client and groq_client.get_groq_chat_config/
get_async_groq_chat_client are all monkeypatched to deterministic fakes -
this module only tests the *branching logic* (which provider's config/
client gets returned for a given LLM_PROVIDER value), not real network
calls."""

from dataclasses import dataclass

import app.engine.azure_client as azure_client_module
import app.engine.groq_client as groq_client_module
import app.engine.llm_provider as llm_provider_module


@dataclass(frozen=True)
class _FakeChatConfig:
    model: str


class _FakeAsyncClient:
    """A stand-in for AsyncAzureOpenAI/AsyncOpenAI - identity is all that
    matters here (proving the *right* fake was returned), not behavior."""


def _unset_llm_provider(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)


def test_get_llm_provider_name_defaults_to_groq_when_unset(monkeypatch):
    _unset_llm_provider(monkeypatch)
    assert llm_provider_module.get_llm_provider_name() == "groq"


def test_get_llm_provider_name_defaults_to_groq_when_blank(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "   ")
    assert llm_provider_module.get_llm_provider_name() == "groq"


def test_get_llm_provider_name_defaults_to_groq_for_unrecognized_value(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    assert llm_provider_module.get_llm_provider_name() == "groq"


def test_get_llm_provider_name_is_case_insensitive_for_azure(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "AZURE")
    assert llm_provider_module.get_llm_provider_name() == "azure"


def test_chat_provider_configured_checks_groq_when_provider_is_groq(monkeypatch):
    _unset_llm_provider(monkeypatch)
    monkeypatch.setattr(groq_client_module, "get_groq_chat_config", lambda: None)
    monkeypatch.setattr(
        azure_client_module,
        "get_chat_config",
        lambda: (_ for _ in ()).throw(AssertionError("azure config must not be checked for provider=groq")),
    )

    assert llm_provider_module.chat_provider_configured() is False

    monkeypatch.setattr(groq_client_module, "get_groq_chat_config", lambda: _FakeChatConfig(model="m"))
    assert llm_provider_module.chat_provider_configured() is True


def test_chat_provider_configured_checks_azure_when_provider_is_azure(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "azure")
    monkeypatch.setattr(azure_client_module, "get_chat_config", lambda: None)
    monkeypatch.setattr(
        groq_client_module,
        "get_groq_chat_config",
        lambda: (_ for _ in ()).throw(AssertionError("groq config must not be checked for provider=azure")),
    )

    assert llm_provider_module.chat_provider_configured() is False

    monkeypatch.setattr(azure_client_module, "get_chat_config", lambda: _FakeChatConfig(model="m"))
    assert llm_provider_module.chat_provider_configured() is True


def test_get_active_chat_provider_returns_groq_client_by_default(monkeypatch):
    _unset_llm_provider(monkeypatch)
    fake_client = _FakeAsyncClient()
    monkeypatch.setattr(groq_client_module, "get_groq_chat_config", lambda: _FakeChatConfig(model="llama-3.3-70b-versatile"))
    monkeypatch.setattr(groq_client_module, "get_async_groq_chat_client", lambda: fake_client)

    provider = llm_provider_module.get_active_chat_provider()

    assert provider.name == "groq"
    assert provider.client is fake_client
    assert provider.model == "llama-3.3-70b-versatile"


def test_get_active_chat_provider_returns_azure_client_when_selected(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "azure")
    fake_client = _FakeAsyncClient()
    monkeypatch.setattr(azure_client_module, "get_chat_config", lambda: _FakeChatConfig(model="gpt-4o-mini"))
    monkeypatch.setattr(azure_client_module, "get_async_chat_client", lambda: fake_client)

    provider = llm_provider_module.get_active_chat_provider()

    assert provider.name == "azure"
    assert provider.client is fake_client
    assert provider.model == "gpt-4o-mini"


def test_get_active_chat_provider_raises_when_selected_provider_not_configured(monkeypatch):
    _unset_llm_provider(monkeypatch)
    monkeypatch.setattr(groq_client_module, "get_groq_chat_config", lambda: None)

    try:
        llm_provider_module.get_active_chat_provider()
        raise AssertionError("expected a RuntimeError")
    except RuntimeError:
        pass
