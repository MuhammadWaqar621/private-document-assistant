"""Unit tests for engine/rag.py's agentic tool-calling pipeline.

Real chat-completion calls are never made here: get_active_chat_provider()
is monkeypatched to return a fake async client whose
chat.completions.create() records the `messages`/`tools` it was called
with and returns a canned async-iterable "stream" of chunks shaped like
the real OpenAI/Groq streaming response - this proves stream_agentic_reply()
builds the right prompt, forwards the right tokens, and only ever calls
retrieve() with server-supplied user_id/chat_id, without touching Azure
OpenAI or Groq."""

from dataclasses import dataclass
from typing import List, Optional

import pytest

from app.engine import rag as rag_module
from app.engine.llm_provider import ActiveChatProvider
from app.engine.qdrant_client import SearchResult


@dataclass
class _FakeToolCallFunction:
    name: Optional[str]
    arguments: Optional[str]


@dataclass
class _FakeToolCall:
    index: int
    id: Optional[str]
    function: Optional[_FakeToolCallFunction]


@dataclass
class _FakeDelta:
    content: Optional[str] = None
    tool_calls: Optional[List[_FakeToolCall]] = None


@dataclass
class _FakeChoice:
    delta: _FakeDelta


@dataclass
class _FakeEvent:
    choices: List[_FakeChoice]


class _FakeEventStream:
    def __init__(self, events: List[_FakeEvent]):
        self._events = events

    def __aiter__(self):
        return self._iterator()

    async def _iterator(self):
        for event in self._events:
            yield event


class _MultiCallChatCompletions:
    """Returns a DIFFERENT canned stream on each successive call - needed
    to simulate the two-phase tool-calling round trip (first call requests
    the tool, second call streams the real final answer after the tool has
    run)."""

    def __init__(self, responses: List[List[_FakeEvent]]):
        self.calls: List[dict] = []
        self._responses = responses

    async def create(self, model, messages, stream, tools=None, tool_choice=None):
        self.calls.append(
            {"model": model, "messages": messages, "stream": stream, "tools": tools, "tool_choice": tool_choice}
        )
        events = self._responses[len(self.calls) - 1]
        return _FakeEventStream(events)


class _FakeAsyncClient:
    def __init__(self, completions: _MultiCallChatCompletions):
        self.chat = type("_Chat", (), {"completions": completions})()


def _content_events(tokens: List[str]) -> List[_FakeEvent]:
    return [_FakeEvent(choices=[_FakeChoice(delta=_FakeDelta(content=t))]) for t in tokens]


def _tool_call_events(name: str, arguments_chunks: List[str], call_id: str = "call_1") -> List[_FakeEvent]:
    events = [
        _FakeEvent(
            choices=[
                _FakeChoice(
                    delta=_FakeDelta(
                        tool_calls=[_FakeToolCall(index=0, id=call_id, function=_FakeToolCallFunction(name=name, arguments=None))]
                    )
                )
            ]
        )
    ]
    for chunk in arguments_chunks:
        events.append(
            _FakeEvent(
                choices=[
                    _FakeChoice(
                        delta=_FakeDelta(
                            tool_calls=[_FakeToolCall(index=0, id=None, function=_FakeToolCallFunction(name=None, arguments=chunk))]
                        )
                    )
                ]
            )
        )
    return events


def _patch_multi_call_provider(monkeypatch, responses: List[List[_FakeEvent]]):
    fake_completions = _MultiCallChatCompletions(responses)
    fake_client = _FakeAsyncClient(fake_completions)
    provider = ActiveChatProvider(name="groq", client=fake_client, model="test-model")
    monkeypatch.setattr(rag_module, "get_active_chat_provider", lambda: provider)
    return fake_completions


async def _collect(agen):
    """Joins only the "token" events into the final answer text - mirrors
    what app/api/messages.py's event_stream() does with full_text, and
    matches what most of these tests care about (the actual answer, not
    the status events interleaved with it)."""
    events = [event async for event in agen]
    return "".join(e.text for e in events if e.type == "token")


async def _collect_statuses(agen):
    events = [event async for event in agen]
    return [e.text for e in events if e.type == "status"]


@pytest.mark.asyncio
async def test_agentic_reply_direct_answer_never_calls_retrieve_or_a_second_completion(monkeypatch):
    # A greeting: the model answers directly in one streamed call, no tool
    # call at all - retrieve() must never be touched.
    def _fail_if_called(*args, **kwargs):
        raise AssertionError("retrieve() must not be called when the model never requests the tool")

    monkeypatch.setattr(rag_module, "retrieve", _fail_if_called)
    fake_completions = _patch_multi_call_provider(monkeypatch, [_content_events(["Hello", "!"])])

    result = await _collect(rag_module.stream_agentic_reply("hi", user_id=1, chat_id=None, history=None))

    assert result == "Hello!"
    assert len(fake_completions.calls) == 1
    assert fake_completions.calls[0]["messages"][0]["content"] == rag_module.AGENT_SYSTEM_PROMPT
    assert fake_completions.calls[0]["tools"] == rag_module.SEARCH_DOCUMENTS_TOOL


@pytest.mark.asyncio
async def test_agentic_reply_includes_prior_history_turns(monkeypatch):
    fake_completions = _patch_multi_call_provider(monkeypatch, [_content_events(["ok"])])
    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "Hello! Ask me anything."},
    ]

    await _collect(rag_module.stream_agentic_reply("who built this?", user_id=1, chat_id=None, history=history))

    roles_and_content = [(m["role"], m["content"]) for m in fake_completions.calls[0]["messages"]]
    assert ("user", "hi") in roles_and_content
    assert ("assistant", "Hello! Ask me anything.") in roles_and_content


@pytest.mark.asyncio
async def test_agentic_reply_tool_call_triggers_a_real_retrieve_and_a_second_streamed_call(monkeypatch):
    recorded_retrieve_calls = []

    def _fake_retrieve(query, user_id, chat_id=None, **kwargs):
        recorded_retrieve_calls.append({"query": query, "user_id": user_id, "chat_id": chat_id})
        return [SearchResult(text="Refunds within 30 days.", page_number=2, filename="policy.pdf", document_id=7, score=0.9)]

    monkeypatch.setattr(rag_module, "retrieve", _fake_retrieve)
    fake_completions = _patch_multi_call_provider(
        monkeypatch,
        [
            _tool_call_events("search_documents", ['{"query": "refund', ' policy"}']),
            _content_events(["30 days", " (policy.pdf, p.2)."]),
        ],
    )

    result = await _collect(
        rag_module.stream_agentic_reply("What's the refund policy?", user_id=42, chat_id=99, history=None)
    )

    assert result == "30 days (policy.pdf, p.2)."
    assert len(fake_completions.calls) == 2
    # The model only ever supplied the query string - user_id/chat_id came
    # from the function's own arguments (the authenticated caller), never
    # from anything the model produced.
    assert recorded_retrieve_calls == [{"query": "refund policy", "user_id": 42, "chat_id": 99}]
    # Second call includes the tool result as a "tool" message.
    second_call_messages = fake_completions.calls[1]["messages"]
    assert any(m.get("role") == "tool" and "Refunds within 30 days" in m.get("content", "") for m in second_call_messages)


@pytest.mark.asyncio
async def test_agentic_reply_tool_call_with_no_matching_chunks_still_gets_a_final_answer(monkeypatch):
    monkeypatch.setattr(rag_module, "retrieve", lambda *a, **k: [])
    fake_completions = _patch_multi_call_provider(
        monkeypatch,
        [
            _tool_call_events("search_documents", ['{"query": "capital of France"}']),
            _content_events(["I couldn't find that in your documents, but Paris is the capital of France."]),
        ],
    )

    result = await _collect(
        rag_module.stream_agentic_reply("What is the capital of France?", user_id=1, chat_id=None, history=None)
    )

    assert "Paris" in result
    second_call_messages = fake_completions.calls[1]["messages"]
    assert any(
        m.get("role") == "tool" and "no matching document excerpts" in m.get("content", "")
        for m in second_call_messages
    )


# --- real-time status events -------------------------------------------


@pytest.mark.asyncio
async def test_direct_answer_emits_a_thinking_status_but_no_search_status(monkeypatch):
    def _fail_if_called(*args, **kwargs):
        raise AssertionError("retrieve() must not be called when the model never requests the tool")

    monkeypatch.setattr(rag_module, "retrieve", _fail_if_called)
    _patch_multi_call_provider(monkeypatch, [_content_events(["Hello", "!"])])

    statuses = await _collect_statuses(
        rag_module.stream_agentic_reply("hi", user_id=1, chat_id=None, history=None)
    )

    assert statuses == ["Thinking..."]


@pytest.mark.asyncio
async def test_tool_call_path_emits_searching_then_found_status_in_order(monkeypatch):
    monkeypatch.setattr(
        rag_module,
        "retrieve",
        lambda *a, **k: [SearchResult(text="x", page_number=1, filename="a.pdf", document_id=1, score=0.9)],
    )
    _patch_multi_call_provider(
        monkeypatch,
        [
            _tool_call_events("search_documents", ['{"query": "refund policy"}']),
            _content_events(["30 days"]),
        ],
    )

    statuses = await _collect_statuses(
        rag_module.stream_agentic_reply("What's the refund policy?", user_id=1, chat_id=None, history=None)
    )

    assert statuses == ["Thinking...", "Searching your documents...", "Found relevant excerpts, writing an answer..."]


@pytest.mark.asyncio
async def test_tool_call_with_no_matches_emits_the_no_documents_found_status(monkeypatch):
    monkeypatch.setattr(rag_module, "retrieve", lambda *a, **k: [])
    _patch_multi_call_provider(
        monkeypatch,
        [
            _tool_call_events("search_documents", ['{"query": "capital of France"}']),
            _content_events(["Paris"]),
        ],
    )

    statuses = await _collect_statuses(
        rag_module.stream_agentic_reply("What is the capital of France?", user_id=1, chat_id=None, history=None)
    )

    assert statuses == [
        "Thinking...",
        "Searching your documents...",
        "No matching documents found, answering from general knowledge...",
    ]


@pytest.mark.asyncio
async def test_status_events_are_never_included_in_the_collected_answer_text(monkeypatch):
    monkeypatch.setattr(
        rag_module,
        "retrieve",
        lambda *a, **k: [SearchResult(text="x", page_number=1, filename="a.pdf", document_id=1, score=0.9)],
    )
    _patch_multi_call_provider(
        monkeypatch,
        [
            _tool_call_events("search_documents", ['{"query": "q"}']),
            _content_events(["final", " answer"]),
        ],
    )

    result = await _collect(
        rag_module.stream_agentic_reply("q", user_id=1, chat_id=None, history=None)
    )

    assert result == "final answer"
    assert "Thinking" not in result
    assert "Searching" not in result
