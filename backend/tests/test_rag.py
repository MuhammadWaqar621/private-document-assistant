"""Unit tests for engine/rag.py's message-building and streaming functions.

Real chat-completion calls are never made here: get_active_chat_provider()
is monkeypatched to return a fake async client whose
chat.completions.create() records the `messages` it was called with and
returns a canned async-iterable "stream" of chunks shaped like the real
OpenAI/Groq streaming response - this proves stream_answer()/
stream_onboarding_reply() build the right prompt and forward the right
tokens, without touching Azure OpenAI or Groq."""

from dataclasses import dataclass
from typing import List, Optional

import pytest

from app.engine import rag as rag_module
from app.engine.llm_provider import ActiveChatProvider
from app.engine.qdrant_client import SearchResult


@dataclass
class _FakeDelta:
    content: Optional[str]


@dataclass
class _FakeChoice:
    delta: _FakeDelta


@dataclass
class _FakeEvent:
    choices: List[_FakeChoice]


class _FakeStream:
    def __init__(self, tokens: List[str]):
        self._tokens = tokens

    def __aiter__(self):
        return self._iterator()

    async def _iterator(self):
        for token in self._tokens:
            yield _FakeEvent(choices=[_FakeChoice(delta=_FakeDelta(content=token))])


class _RecordingChatCompletions:
    def __init__(self, tokens: List[str]):
        self.calls: List[dict] = []
        self._tokens = tokens

    async def create(self, model, messages, stream):
        self.calls.append({"model": model, "messages": messages, "stream": stream})
        return _FakeStream(self._tokens)


class _RecordingChat:
    def __init__(self, tokens: List[str]):
        self.completions = _RecordingChatCompletions(tokens)


class _FakeAsyncClient:
    def __init__(self, tokens: List[str]):
        self.chat = _RecordingChat(tokens)


def _patch_provider(monkeypatch, tokens, name="groq", model="llama-3.3-70b-versatile"):
    fake_client = _FakeAsyncClient(tokens)
    provider = ActiveChatProvider(name=name, client=fake_client, model=model)
    monkeypatch.setattr(rag_module, "get_active_chat_provider", lambda: provider)
    return fake_client


async def _collect(agen):
    return "".join([token async for token in agen])


@pytest.mark.asyncio
async def test_stream_answer_uses_grounded_prompt_when_chunks_present(monkeypatch):
    fake_client = _patch_provider(monkeypatch, ["Paris", " is", " the capital."])
    chunk = SearchResult(text="France's capital is Paris.", page_number=1, filename="geo.pdf", document_id=1, score=0.9)

    result = await _collect(rag_module.stream_answer("What is the capital of France?", [chunk], history=None))

    assert result == "Paris is the capital."
    call = fake_client.chat.completions.calls[0]
    assert call["messages"][0]["content"] == rag_module.GROUNDED_SYSTEM_PROMPT
    assert "Document excerpts:" in call["messages"][-1]["content"]
    assert "geo.pdf" in call["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_stream_answer_uses_fallback_prompt_when_no_chunks_matched(monkeypatch):
    fake_client = _patch_provider(monkeypatch, ["I don't know."])

    await _collect(rag_module.stream_answer("Unrelated question", [], history=None))

    call = fake_client.chat.completions.calls[0]
    assert call["messages"][0]["content"] == rag_module.UNGROUNDED_FALLBACK_SYSTEM_PROMPT
    assert call["messages"][-1]["content"] == "Unrelated question"


@pytest.mark.asyncio
async def test_stream_answer_forwards_model_from_active_provider(monkeypatch):
    fake_client = _patch_provider(monkeypatch, ["ok"], model="a-specific-model")

    await _collect(rag_module.stream_answer("hi", [], history=None))

    assert fake_client.chat.completions.calls[0]["model"] == "a-specific-model"


# --- stream_onboarding_reply -----------------------------------------------


@pytest.mark.asyncio
async def test_stream_onboarding_reply_uses_the_onboarding_system_prompt(monkeypatch):
    fake_client = _patch_provider(monkeypatch, ["Hi", " there!"])

    result = await _collect(rag_module.stream_onboarding_reply("hello", history=None))

    assert result == "Hi there!"
    call = fake_client.chat.completions.calls[0]
    assert call["messages"][0]["content"] == rag_module.ONBOARDING_SYSTEM_PROMPT
    # Never the grounded/fallback prompts used when documents exist.
    assert call["messages"][0]["content"] != rag_module.GROUNDED_SYSTEM_PROMPT
    assert call["messages"][0]["content"] != rag_module.UNGROUNDED_FALLBACK_SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_stream_onboarding_reply_never_includes_document_excerpts(monkeypatch):
    # Structurally, this function takes no chunks/user_id/chat_id at all -
    # no Qdrant retrieval is even possible here - but assert on the built
    # message content too, as a concrete, unambiguous proof.
    fake_client = _patch_provider(monkeypatch, ["ok"])

    await _collect(rag_module.stream_onboarding_reply("what is querynest?", history=None))

    call = fake_client.chat.completions.calls[0]
    for message in call["messages"]:
        assert "Document excerpts:" not in message["content"]
    assert call["messages"][-1]["content"] == "what is querynest?"


@pytest.mark.asyncio
async def test_stream_onboarding_reply_includes_prior_history_turns(monkeypatch):
    fake_client = _patch_provider(monkeypatch, ["ok"])
    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "Hello! Ask me anything."},
    ]

    await _collect(rag_module.stream_onboarding_reply("who built this?", history=history))

    call = fake_client.chat.completions.calls[0]
    roles_and_content = [(m["role"], m["content"]) for m in call["messages"]]
    assert ("user", "hi") in roles_and_content
    assert ("assistant", "Hello! Ask me anything.") in roles_and_content
