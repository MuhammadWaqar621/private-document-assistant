"""Integration tests for /api/chats/* - basic CRUD plus the ownership
check that the rest of the app (documents, messages) builds on: a chat
that exists but belongs to a different user must 404, indistinguishable
from a chat that was never created."""

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
