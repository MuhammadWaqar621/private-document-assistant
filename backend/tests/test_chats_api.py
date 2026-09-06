"""Integration tests for /api/chats/* - basic CRUD plus the ownership
check that the rest of the app (documents, messages) builds on: a chat
that exists but belongs to a different user must 404, indistinguishable
from a chat that was never created."""

import app.api.messages as messages_module

from .conftest import auth_headers, signup


def _create_chat(client, token_body, title=None):
    payload = {"title": title} if title else {}
    response = client.post("/api/chats", json=payload, headers=auth_headers(token_body))
    assert response.status_code == 201, response.text
    return response.json()


def test_create_chat_defaults_title(client):
    user = signup(client)
    chat = _create_chat(client, user)
    assert chat["title"] == "New chat"
    assert "id" in chat


def test_create_chat_with_custom_title(client):
    user = signup(client)
    chat = _create_chat(client, user, title="Q3 budget review")
    assert chat["title"] == "Q3 budget review"


# --- "at most one empty untitled chat" reuse (POST /api/chats) -----------


def test_create_chat_reuses_an_existing_empty_untitled_chat(client):
    user = signup(client)
    first = _create_chat(client, user)

    response = client.post("/api/chats", json={}, headers=auth_headers(user))

    # Reusing an existing empty chat is a 200 (nothing new created), not
    # a 201 - but it's still the SAME chat either way, which is the part
    # the frontend's "New chat" button actually depends on.
    assert response.status_code == 200
    assert response.json()["id"] == first["id"]

    listing = client.get("/api/chats", headers=auth_headers(user))
    assert len(listing.json()) == 1


def test_create_chat_does_not_reuse_a_chat_that_already_has_messages(client, monkeypatch):
    monkeypatch.setattr(messages_module, "azure_ai_configured", lambda: True)

    async def _fake_stream(*args, **kwargs):
        yield "Hello!"

    monkeypatch.setattr(messages_module, "stream_agentic_reply", _fake_stream)

    user = signup(client)
    first = _create_chat(client, user)
    client.post(
        f"/api/chats/{first['id']}/messages", json={"content": "hi"}, headers=auth_headers(user)
    )

    # `first` is no longer titled "New chat" (auto-titled from its first
    # message) - creating again must NOT reuse it, since it isn't empty
    # anymore, regardless of what the client's own local state thinks its
    # title is.
    response = client.post("/api/chats", json={}, headers=auth_headers(user))

    assert response.status_code == 201
    assert response.json()["id"] != first["id"]

    listing = client.get("/api/chats", headers=auth_headers(user))
    assert len(listing.json()) == 2


def test_create_chat_with_an_explicit_title_is_never_reused(client):
    user = signup(client)
    first = _create_chat(client, user, title="Q3 budget review")

    response = client.post(
        "/api/chats", json={"title": "Q3 budget review"}, headers=auth_headers(user)
    )

    assert response.status_code == 201
    assert response.json()["id"] != first["id"]


def test_list_chats_only_returns_the_current_users_chats(client):
    user_a = signup(client)
    user_b = signup(client)

    chat_a = _create_chat(client, user_a, title="A's chat")
    _create_chat(client, user_b, title="B's chat")

    response = client.get("/api/chats", headers=auth_headers(user_a))
    assert response.status_code == 200
    titles = [c["title"] for c in response.json()]
    assert titles == ["A's chat"]
    assert response.json()[0]["id"] == chat_a["id"]


def test_get_chat_returns_its_messages(client):
    user = signup(client)
    chat = _create_chat(client, user)

    response = client.get(f"/api/chats/{chat['id']}", headers=auth_headers(user))

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == chat["id"]
    assert body["messages"] == []


# --- ownership: another user's chat must 404, not 403 --------------------


def test_get_chat_404s_for_another_users_chat(client):
    owner = signup(client)
    intruder = signup(client)
    chat = _create_chat(client, owner)

    response = client.get(f"/api/chats/{chat['id']}", headers=auth_headers(intruder))

    assert response.status_code == 404
    assert response.json()["detail"] == "Chat not found"


def test_get_chat_404s_for_a_chat_that_never_existed(client):
    user = signup(client)

    response = client.get("/api/chats/999999", headers=auth_headers(user))

    assert response.status_code == 404
    assert response.json()["detail"] == "Chat not found"


def test_delete_chat_404s_for_another_users_chat(client):
    owner = signup(client)
    intruder = signup(client)
    chat = _create_chat(client, owner)

    response = client.delete(f"/api/chats/{chat['id']}", headers=auth_headers(intruder))

    assert response.status_code == 404
    # And the chat must still exist for its real owner afterwards.
    still_there = client.get(f"/api/chats/{chat['id']}", headers=auth_headers(owner))
    assert still_there.status_code == 200


def test_delete_chat_succeeds_for_its_owner(client):
    owner = signup(client)
    chat = _create_chat(client, owner)

    response = client.delete(f"/api/chats/{chat['id']}", headers=auth_headers(owner))
    assert response.status_code == 204

    gone = client.get(f"/api/chats/{chat['id']}", headers=auth_headers(owner))
    assert gone.status_code == 404


def test_chats_endpoints_require_authentication(client):
    response = client.get("/api/chats")
    assert response.status_code == 401
