"""Integration tests for /api/chats/{chat_id}/messages - the auto-titling
side effect and the "no documents anywhere" hard gate (refuses before ever
calling the LLM, rather than letting this endpoint silently become a
generic no-grounding chatbot - see app/api/messages.py's module docstring
and the has_any_ready_document check).

Real Azure OpenAI calls are never made in these tests: the hard-gate path
never reaches app.engine.rag at all, which is exactly what's being
proven here by monkeypatching retrieve()/stream_answer() to raise if
called - so azure_ai_configured only needs to be faked True to get past
the initial config check, no real credentials involved.
"""

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


def test_returns_503_when_azure_not_configured(client, monkeypatch):
    monkeypatch.setattr(messages_module, "azure_ai_configured", lambda: False)
    user = signup(client)
    chat = _create_chat(client, user)

    response = _send_message(client, user, chat["id"], "hello")

    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "azure_ai_not_configured"


def test_no_documents_anywhere_refuses_without_calling_the_llm(client, monkeypatch):
    monkeypatch.setattr(messages_module, "azure_ai_configured", lambda: True)
    monkeypatch.setattr(messages_module, "retrieve", _fail_if_called)
    monkeypatch.setattr(messages_module, "stream_answer", _fail_if_called)

    user = signup(client)
    chat = _create_chat(client, user)

    response = _send_message(client, user, chat["id"], "What does my document say?")

    assert response.status_code == 200
    assert "haven't uploaded any documents" in response.text


def test_no_documents_refusal_respects_chat_only_scope_wording(client, monkeypatch):
    monkeypatch.setattr(messages_module, "azure_ai_configured", lambda: True)
    monkeypatch.setattr(messages_module, "retrieve", _fail_if_called)
    monkeypatch.setattr(messages_module, "stream_answer", _fail_if_called)

    user = signup(client)
    chat = _create_chat(client, user)

    response = _send_message(client, user, chat["id"], "hello?", scope="chat")

    assert response.status_code == 200
    assert "for this chat" in response.text


def test_first_message_auto_titles_the_chat(client, monkeypatch):
    monkeypatch.setattr(messages_module, "azure_ai_configured", lambda: True)
    monkeypatch.setattr(messages_module, "retrieve", _fail_if_called)
    monkeypatch.setattr(messages_module, "stream_answer", _fail_if_called)

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
