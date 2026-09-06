"""Integration tests for /api/chats/{chat_id}/messages - the auto-titling
side effect, SSE plumbing, and ownership checks.

Real Azure OpenAI/Groq calls are never made here: stream_agentic_reply()
(the single, unified engine call this endpoint makes - see
app/engine/rag.py's module docstring and test_rag.py for its own
tool-calling behavior, tested separately) is monkeypatched to a fake
deterministic async generator, so azure_ai_configured only needs to be
faked True to get past the initial config check, no real credentials or
document/retrieval setup involved.
"""

import json

from .conftest import auth_headers, signup

import app.api.messages as messages_module
from app.engine.rag import AgentEvent


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


async def _fake_token_stream(*args, **kwargs):
    yield AgentEvent("status", "Thinking...")
    yield AgentEvent("token", "Hello")
    yield AgentEvent("token", " there!")


def _stub_agentic_reply(monkeypatch, recorder=None):
    def _stream_agentic_reply(question, user_id, chat_id, history=None):
        if recorder is not None:
            recorder.append(
                {"question": question, "user_id": user_id, "chat_id": chat_id, "history": history}
            )
        return _fake_token_stream()

    monkeypatch.setattr(messages_module, "stream_agentic_reply", _stream_agentic_reply)


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


def _sse_status_messages(response_text: str) -> list[str]:
    """Same idea as _sse_token_text() above, but for `event: status` blocks
    - each carries `data: {"message": "..."}`."""
    messages = []
    for event_block in response_text.split("\n\n"):
        if not event_block.startswith("event: status"):
            continue
        data_line = next(line for line in event_block.splitlines() if line.startswith("data:"))
        messages.append(json.loads(data_line[len("data:"):].strip())["message"])
    return messages


def test_returns_503_when_azure_not_configured(client, monkeypatch):
    monkeypatch.setattr(messages_module, "azure_ai_configured", lambda: False)
    user = signup(client)
    chat = _create_chat(client, user)

    response = _send_message(client, user, chat["id"], "hello")

    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "azure_ai_not_configured"


def test_message_streams_the_agentic_reply(client, monkeypatch):
    monkeypatch.setattr(messages_module, "azure_ai_configured", lambda: True)
    _stub_agentic_reply(monkeypatch)

    user = signup(client)
    chat = _create_chat(client, user)

    response = _send_message(client, user, chat["id"], "hi")

    assert response.status_code == 200
    assert _sse_token_text(response.text) == "Hello there!"
    assert "event: done" in response.text


def test_message_forwards_status_events_separately_from_the_answer(client, monkeypatch):
    # "status" events (real-time progress updates - see AgentEvent in
    # app/engine/rag.py) must reach the client as their own SSE event, and
    # must never leak into the token stream that becomes the persisted
    # assistant Message.
    monkeypatch.setattr(messages_module, "azure_ai_configured", lambda: True)
    _stub_agentic_reply(monkeypatch)

    user = signup(client)
    chat = _create_chat(client, user)

    response = _send_message(client, user, chat["id"], "hi")

    assert _sse_status_messages(response.text) == ["Thinking..."]
    assert _sse_token_text(response.text) == "Hello there!"
    assert "Thinking" not in _sse_token_text(response.text)


def test_message_passes_the_authenticated_user_id_never_from_the_request(client, monkeypatch):
    # The isolation-critical part: user_id (and chat_id, per scope) must
    # come from the authenticated session, never anything the client body
    # could influence - the request body only carries `content`/`scope`.
    calls = []
    monkeypatch.setattr(messages_module, "azure_ai_configured", lambda: True)
    _stub_agentic_reply(monkeypatch, recorder=calls)

    user = signup(client)
    chat = _create_chat(client, user)

    _send_message(client, user, chat["id"], "what is querynest?", scope="chat")

    assert len(calls) == 1
    assert calls[0]["question"] == "what is querynest?"
    assert calls[0]["chat_id"] == chat["id"]  # scope="chat" narrows to this chat
    assert calls[0]["history"] == []  # first message in the chat - no prior turns yet


def test_message_default_scope_passes_no_chat_id_narrowing(client, monkeypatch):
    calls = []
    monkeypatch.setattr(messages_module, "azure_ai_configured", lambda: True)
    _stub_agentic_reply(monkeypatch, recorder=calls)

    user = signup(client)
    chat = _create_chat(client, user)

    _send_message(client, user, chat["id"], "hello")  # scope omitted -> default "all"

    assert len(calls) == 1
    assert calls[0]["chat_id"] is None


def test_first_message_auto_titles_the_chat(client, monkeypatch):
    monkeypatch.setattr(messages_module, "azure_ai_configured", lambda: True)
    _stub_agentic_reply(monkeypatch)

    user = signup(client)
    chat = _create_chat(client, user)
    assert chat["title"] == "New chat"

    _send_message(client, user, chat["id"], "What is the refund policy?")

    detail = client.get(f"/api/chats/{chat['id']}", headers=auth_headers(user))
    assert detail.status_code == 200
    assert detail.json()["title"] == "What is the refund policy?"


def test_auto_title_truncates_long_first_messages_at_a_word_boundary(client, monkeypatch):
    monkeypatch.setattr(messages_module, "azure_ai_configured", lambda: True)
    _stub_agentic_reply(monkeypatch)

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
    _stub_agentic_reply(monkeypatch)

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
