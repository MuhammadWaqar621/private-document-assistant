"""Integration tests for /api/chats/{chat_id}/documents/* - ownership
checks (a chat belonging to another user 404s, same as app/api/chats.py)
and the upload -> status transition (processing -> ready / failed).

Real Azure OpenAI calls are never made in these tests: `azure_ai_configured`
and `ingest_document` (both imported by name into app/api/documents.py) are
monkeypatched to fake, deterministic results, so the test exercises the
*endpoint's* status-transition logic (what it does with an
IngestionResult) rather than the engine's real ingestion pipeline, which
is covered separately by test_chunking.py/test_extraction.py/
test_qdrant_isolation.py.
"""

import app.api.documents as documents_module
from app.engine.ingestion import IngestionResult

from .conftest import auth_headers, signup


def _create_chat(client, token_body):
    response = client.post("/api/chats", json={}, headers=auth_headers(token_body))
    assert response.status_code == 201
    return response.json()


def _upload(client, token_body, chat_id, filename="notes.txt", content=b"hello world"):
    return client.post(
        f"/api/chats/{chat_id}/documents",
        headers=auth_headers(token_body),
        files={"file": (filename, content, "text/plain")},
    )


def test_upload_returns_503_when_azure_not_configured(client, monkeypatch):
    monkeypatch.setattr(documents_module, "azure_ai_configured", lambda: False)
    user = signup(client)
    chat = _create_chat(client, user)

    response = _upload(client, user, chat["id"])

    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "azure_ai_not_configured"


def test_upload_transitions_to_ready_on_successful_ingestion(client, monkeypatch, tmp_path):
    monkeypatch.setattr(documents_module, "azure_ai_configured", lambda: True)
    monkeypatch.setattr(documents_module, "STORAGE_ROOT", tmp_path)
    monkeypatch.setattr(
        documents_module,
        "ingest_document",
        lambda **kwargs: IngestionResult(success=True, chunk_count=3),
    )

    user = signup(client)
    chat = _create_chat(client, user)

    response = _upload(client, user, chat["id"])

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "ready"
    assert body["error_message"] is None
    assert body["filename"] == "notes.txt"

    # The raw bytes were actually written to disk under STORAGE_ROOT.
    written = list(tmp_path.rglob("original.*"))
    assert len(written) == 1


def test_upload_transitions_to_failed_on_ingestion_failure(client, monkeypatch, tmp_path):
    monkeypatch.setattr(documents_module, "azure_ai_configured", lambda: True)
    monkeypatch.setattr(documents_module, "STORAGE_ROOT", tmp_path)
    monkeypatch.setattr(
        documents_module,
        "ingest_document",
        lambda **kwargs: IngestionResult(success=False, error_message="Embedding failed: boom"),
    )

    user = signup(client)
    chat = _create_chat(client, user)

    response = _upload(client, user, chat["id"])

    # Ingestion failures never crash the request - still a 201 with the
    # Document row reflecting status=failed + error_message.
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "failed"
    assert body["error_message"] == "Embedding failed: boom"


def test_list_documents_scoped_to_the_chat_and_owner(client, monkeypatch, tmp_path):
    monkeypatch.setattr(documents_module, "azure_ai_configured", lambda: True)
    monkeypatch.setattr(documents_module, "STORAGE_ROOT", tmp_path)
    monkeypatch.setattr(
        documents_module,
        "ingest_document",
        lambda **kwargs: IngestionResult(success=True, chunk_count=1),
    )

    user = signup(client)
    chat = _create_chat(client, user)
    _upload(client, user, chat["id"], filename="a.txt")

    response = client.get(f"/api/chats/{chat['id']}/documents", headers=auth_headers(user))

    assert response.status_code == 200
    docs = response.json()
    assert len(docs) == 1
    assert docs[0]["filename"] == "a.txt"


# --- ownership: another user's chat must 404 for every document route ----


def test_upload_404s_for_another_users_chat(client):
    owner = signup(client)
    intruder = signup(client)
    chat = _create_chat(client, owner)

    response = _upload(client, intruder, chat["id"])

    assert response.status_code == 404
    assert response.json()["detail"] == "Chat not found"


def test_list_documents_404s_for_another_users_chat(client):
    owner = signup(client)
    intruder = signup(client)
    chat = _create_chat(client, owner)

    response = client.get(
        f"/api/chats/{chat['id']}/documents", headers=auth_headers(intruder)
    )

    assert response.status_code == 404


def test_delete_document_404s_for_another_users_chat(client, monkeypatch, tmp_path):
    monkeypatch.setattr(documents_module, "azure_ai_configured", lambda: True)
    monkeypatch.setattr(documents_module, "STORAGE_ROOT", tmp_path)
    monkeypatch.setattr(
        documents_module,
        "ingest_document",
        lambda **kwargs: IngestionResult(success=True, chunk_count=1),
    )
    # Avoid touching the real Qdrant collection for this ownership-only
    # test - delete_document is never expected to be reached here since
    # the chat-ownership check 404s first.
    monkeypatch.setattr(documents_module, "qdrant_delete_document", lambda document_id: None)

    owner = signup(client)
    intruder = signup(client)
    chat = _create_chat(client, owner)
    doc = _upload(client, owner, chat["id"]).json()

    response = client.delete(
        f"/api/chats/{chat['id']}/documents/{doc['id']}", headers=auth_headers(intruder)
    )

    assert response.status_code == 404


def test_delete_document_succeeds_for_its_owner(client, monkeypatch, tmp_path):
    monkeypatch.setattr(documents_module, "azure_ai_configured", lambda: True)
    monkeypatch.setattr(documents_module, "STORAGE_ROOT", tmp_path)
    monkeypatch.setattr(
        documents_module,
        "ingest_document",
        lambda **kwargs: IngestionResult(success=True, chunk_count=1),
    )
    monkeypatch.setattr(documents_module, "qdrant_delete_document", lambda document_id: None)

    owner = signup(client)
    chat = _create_chat(client, owner)
    doc = _upload(client, owner, chat["id"]).json()

    response = client.delete(
        f"/api/chats/{chat['id']}/documents/{doc['id']}", headers=auth_headers(owner)
    )
    assert response.status_code == 204

    listing = client.get(f"/api/chats/{chat['id']}/documents", headers=auth_headers(owner))
    assert listing.json() == []
