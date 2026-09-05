"""Integration tests for /api/chats/{chat_id}/messages - the auto-titling
side effect and the "no documents anywhere" hard gate (refuses to run real
document retrieval before ever calling the LLM with document context,
rather than letting this endpoint silently become a generic no-grounding
chatbot - see app/api/messages.py's module docstring and the
has_any_ready_document check).

Real Azure OpenAI/Groq calls are never made in these tests:
retrieve()/stream_answer() are monkeypatched to raise if called (the
no-documents path must never reach them), and stream_onboarding_reply() -
the real-but-scoped LLM call used for that no-documents case (see
app/engine/rag.py's ONBOARDING_SYSTEM_PROMPT) - is monkeypatched to a fake
deterministic async generator, so azure_ai_configured only needs to be
faked True to get past the initial config check, no real credentials
involved.
"""

import json

from .conftest import auth_headers, signup

import app.api.messages as messages_module


def _create_chat(client, token_body, title=None):
    response = client.post(
        "/api/chats", json={"title": title} if title else {}, headers=auth_headers(token_body)
    )
    assert response.status_code == 201, response.text
    return response.json()


def _send_message(client, token_body, chat_id, content, scope=None):
    body = {"content": content}
    if scope is not None:
        body["scope"] = scope
    return client.post(
        f"/api/chats/{chat_id}/messages", json=body, headers=auth_headers(token_body)
    )


def _fail_if_called(*args, **kwargs):
    raise AssertionError(
        "retrieve()/stream_answer() must not be called when the user has "
        "no ready documents anywhere in scope - this is the hard gate the "
        "whole 'not a generic chatbot' guarantee depends on."
    )


async def _fake_token_stream(*args, **kwargs):
    yield "Hello"
    yield " there!"


def _stub_onboarding_reply(monkeypatch, recorder=None):
    def _stream_onboarding_reply(question, history=None):
        if recorder is not None:
            recorder.append({"question": question, "history": history})
        return _fake_token_stream()

    monkeypatch.setattr(messages_module, "stream_onboarding_reply", _stream_onboarding_reply)


def _sse_token_text(response_text: str) -> str:
    """Reconstruct the full streamed reply from raw SSE response text -
    each `event: token` carries one `data: {"content": "..."}` chunk (see
    app/api/messages.py's _sse() helper), so this joins them back together
    in order. Used instead of re-fetching the chat afterward: the final
    assistant Message is written via a fresh SessionLocal() opened
    directly in app/api/messages.py's event_stream() (not through the
    request-scoped `db` dependency this test suite overrides to SQLite -
    see conftest.py), so it's never visible to a GET against the test's
    isolated database - the SSE stream itself is the only reliable place
    to observe the streamed content in this test setup."""
    tokens = []
    for event_block in response_text.split("\n\n"):
        if not event_block.startswith("event: token"):
            continue
        data_line = next(line for line in event_block.splitlines() if line.startswith("data:"))
        tokens.append(json.loads(data_line[len("data:"):].strip())["content"])
    return "".join(tokens)


def test_returns_503_when_azure_not_configured(client, monkeypatch):
    monkeypatch.setattr(messages_module, "azure_ai_configured", lambda: False)
    user = signup(client)
    chat = _create_chat(client, user)

    response = _send_message(client, user, chat["id"], "hello")

    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "azure_ai_not_configured"


def test_no_documents_anywhere_uses_onboarding_reply_not_retrieval(client, monkeypatch):
    monkeypatch.setattr(messages_module, "azure_ai_configured", lambda: True)
    monkeypatch.setattr(messages_module, "retrieve", _fail_if_called)
    monkeypatch.setattr(messages_module, "stream_answer", _fail_if_called)
    _stub_onboarding_reply(monkeypatch)

    user = signup(client)
    chat = _create_chat(client, user)

    response = _send_message(client, user, chat["id"], "hi")

    assert response.status_code == 200
    assert _sse_token_text(response.text) == "Hello there!"


def test_no_documents_onboarding_reply_receives_the_question_and_history(client, monkeypatch):
    monkeypatch.setattr(messages_module, "azure_ai_configured", lambda: True)
    monkeypatch.setattr(messages_module, "retrieve", _fail_if_called)
    monkeypatch.setattr(messages_module, "stream_answer", _fail_if_called)
    calls = []
    _stub_onboarding_reply(monkeypatch, recorder=calls)

    user = signup(client)
    chat = _create_chat(client, user)

    response = _send_message(client, user, chat["id"], "what is querynest?", scope="chat")

    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0]["question"] == "what is querynest?"
    assert calls[0]["history"] == []  # first message in the chat - no prior turns yet


def test_no_documents_onboarding_reply_streams_the_full_reconstructed_text(client, monkeypatch):
    # The assistant Message row for this path is written via a fresh
    # SessionLocal() inside event_stream() (see app/api/messages.py),
    # which - unlike the request-scoped `db` dependency - isn't overridden
    # to this test suite's isolated SQLite database (see conftest.py), so
    # it can't be observed via a GET afterward here. What *can* be
    # verified in this test setup is that the full reply is correctly
    # streamed back token-by-token over SSE - see _sse_token_text().
    monkeypatch.setattr(messages_module, "azure_ai_configured", lambda: True)
    monkeypatch.setattr(messages_module, "retrieve", _fail_if_called)
    monkeypatch.setattr(messages_module, "stream_answer", _fail_if_called)
    _stub_onboarding_reply(monkeypatch)

    user = signup(client)
    chat = _create_chat(client, user)

    response = _send_message(client, user, chat["id"], "hello?", scope="chat")

    assert response.status_code == 200
    assert _sse_token_text(response.text) == "Hello there!"
    assert "event: done" in response.text


def test_first_message_auto_titles_the_chat(client, monkeypatch):
    monkeypatch.setattr(messages_module, "azure_ai_configured", lambda: True)
    monkeypatch.setattr(messages_module, "retrieve", _fail_if_called)
    monkeypatch.setattr(messages_module, "stream_answer", _fail_if_called)
    _stub_onboarding_reply(monkeypatch)

    user = signup(client)
    chat = _create_chat(client, user)
    assert chat["title"] == "New chat"

    _send_message(client, user, chat["id"], "What is the refund policy?")

    detail = client.get(f"/api/chats/{chat['id']}", headers=auth_headers(user))
    assert detail.status_code == 200
    assert detail.json()["title"] == "What is the refund policy?"


def test_auto_title_truncates_long_first_messages_at_a_word_boundary(client, monkeypatch):
    monkeypatch.setattr(messages_module, "azure_ai_configured", lambda: True)
    monkeypatch.setattr(messages_module, "retrieve", _fail_if_called)
    monkeypatch.setattr(messages_module, "stream_answer", _fail_if_called)
    _stub_onboarding_reply(monkeypatch)

    user = signup(client)
    chat = _create_chat(client, user)

    long_message = "word " * 30  # far past the 50-char title cap
    _send_message(client, user, chat["id"], long_message)

    detail = client.get(f"/api/chats/{chat['id']}", headers=auth_headers(user))
    title = detail.json()["title"]
    assert title.endswith("...")
    assert len(title) <= 53  # 50 + "..."
    assert " " not in title[-4:-3]  # didn't cut mid-word right before the ellipsis


def test_auto_title_never_overwrites_an_explicit_title(client, monkeypatch):
    monkeypatch.setattr(messages_module, "azure_ai_configured", lambda: True)
    monkeypatch.setattr(messages_module, "retrieve", _fail_if_called)
    monkeypatch.setattr(messages_module, "stream_answer", _fail_if_called)
    _stub_onboarding_reply(monkeypatch)

    user = signup(client)
    chat = _create_chat(client, user, title="My custom title")

    _send_message(client, user, chat["id"], "hello")

    detail = client.get(f"/api/chats/{chat['id']}", headers=auth_headers(user))
    assert detail.json()["title"] == "My custom title"


def test_messages_endpoint_404s_for_another_users_chat(client, monkeypatch):
    monkeypatch.setattr(messages_module, "azure_ai_configured", lambda: True)

    owner = signup(client)
    chat = _create_chat(client, owner)

    intruder = signup(client)
    response = _send_message(client, intruder, chat["id"], "hello")

    assert response.status_code == 404
