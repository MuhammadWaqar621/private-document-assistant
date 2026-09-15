"""Integration tests for /api/chats/{chat_id}/documents/* - ownership
checks (a chat belonging to another user 404s, same as app/api/chats.py)
and the upload -> status transition (processing -> ready / failed).

Real Azure OpenAI calls are never made in these tests: `azure_ai_configured`
and `ingest_document` (both imported by name into app/api/documents.py) are
monkeypatched to fake, deterministic results, so the test exercises the
*endpoint's* status-transition logic (what it does with an
IngestionResult) rather than the engine's real ingestion pipeline, which
is covered separately by test_chunking.py/test_extraction.py/
test_vector_store_isolation.py. Similarly, `upload_blob`/`delete_blob`
(Vercel Blob - app/engine/blob_storage.py) are monkeypatched to an
in-memory fake so these tests never make a real network call or require
BLOB_READ_WRITE_TOKEN to be set.
"""

import app.api.documents as documents_module
from app.engine.blob_storage import UploadedBlob
from app.engine.ingestion import IngestionResult

from .conftest import auth_headers, signup


def _fake_upload_blob(store):
    """Returns a fake `upload_blob` that records (pathname -> bytes) in
    `store` instead of calling the real Vercel Blob API, and hands back a
    deterministic fake URL - mirrors what monkeypatching STORAGE_ROOT used
    to do for the old local-disk implementation."""

    def _upload(pathname, data, content_type=None):
        store[pathname] = data
        return UploadedBlob(url=f"https://example-blob.vercel-storage.com/{pathname}", pathname=pathname)

    return _upload


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


def _upload_library(client, token_body, filename="notes.txt", content=b"hello world"):
    return client.post(
        "/api/documents",
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


def test_upload_transitions_to_ready_on_successful_ingestion(client, monkeypatch):
    blob_store: dict = {}
    monkeypatch.setattr(documents_module, "azure_ai_configured", lambda: True)
    monkeypatch.setattr(documents_module, "upload_blob", _fake_upload_blob(blob_store))
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

    # The raw bytes were actually uploaded to Vercel Blob (faked here).
    assert len(blob_store) == 1
    [(pathname, data)] = blob_store.items()
    assert pathname.endswith("/original.txt")
    assert data == b"hello world"


def test_upload_transitions_to_failed_on_ingestion_failure(client, monkeypatch):
    monkeypatch.setattr(documents_module, "azure_ai_configured", lambda: True)
    monkeypatch.setattr(documents_module, "upload_blob", _fake_upload_blob({}))
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


def test_list_documents_scoped_to_the_chat_and_owner(client, monkeypatch):
    monkeypatch.setattr(documents_module, "azure_ai_configured", lambda: True)
    monkeypatch.setattr(documents_module, "upload_blob", _fake_upload_blob({}))
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


def test_delete_document_404s_for_another_users_chat(client, monkeypatch):
    monkeypatch.setattr(documents_module, "azure_ai_configured", lambda: True)
    monkeypatch.setattr(documents_module, "upload_blob", _fake_upload_blob({}))
    monkeypatch.setattr(
        documents_module,
        "ingest_document",
        lambda **kwargs: IngestionResult(success=True, chunk_count=1),
    )
    # Avoid touching the real vector store for this ownership-only
    # test - delete_document is never expected to be reached here since
    # the chat-ownership check 404s first.
    monkeypatch.setattr(documents_module, "delete_document_vectors", lambda document_id: None)

    owner = signup(client)
    intruder = signup(client)
    chat = _create_chat(client, owner)
    doc = _upload(client, owner, chat["id"]).json()

    response = client.delete(
        f"/api/chats/{chat['id']}/documents/{doc['id']}", headers=auth_headers(intruder)
    )

    assert response.status_code == 404


def test_delete_document_succeeds_for_its_owner(client, monkeypatch):
    monkeypatch.setattr(documents_module, "azure_ai_configured", lambda: True)
    monkeypatch.setattr(documents_module, "upload_blob", _fake_upload_blob({}))
    monkeypatch.setattr(
        documents_module,
        "ingest_document",
        lambda **kwargs: IngestionResult(success=True, chunk_count=1),
    )
    monkeypatch.setattr(documents_module, "delete_document_vectors", lambda document_id: None)

    owner = signup(client)
    chat = _create_chat(client, owner)
    doc = _upload(client, owner, chat["id"]).json()

    response = client.delete(
        f"/api/chats/{chat['id']}/documents/{doc['id']}", headers=auth_headers(owner)
    )
    assert response.status_code == 204

    listing = client.get(f"/api/chats/{chat['id']}/documents", headers=auth_headers(owner))
    assert listing.json() == []


# --- account-level "library" documents (POST /api/documents, no chat_id) --


def test_library_upload_requires_no_chat_and_transitions_to_ready(client, monkeypatch):
    monkeypatch.setattr(documents_module, "azure_ai_configured", lambda: True)
    monkeypatch.setattr(documents_module, "upload_blob", _fake_upload_blob({}))
    captured_kwargs = {}

    def _fake_ingest(**kwargs):
        captured_kwargs.update(kwargs)
        return IngestionResult(success=True, chunk_count=2)

    monkeypatch.setattr(documents_module, "ingest_document", _fake_ingest)

    user = signup(client)
    response = _upload_library(client, user, filename="handbook.pdf")

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "ready"
    assert body["filename"] == "handbook.pdf"
    # The shared ingestion pipeline is called with chat_id=None - never a
    # chat, since this document isn't tied to one.
    assert captured_kwargs["chat_id"] is None


def test_library_documents_are_separate_from_chat_documents(client, monkeypatch):
    monkeypatch.setattr(documents_module, "azure_ai_configured", lambda: True)
    monkeypatch.setattr(documents_module, "upload_blob", _fake_upload_blob({}))
    monkeypatch.setattr(
        documents_module,
        "ingest_document",
        lambda **kwargs: IngestionResult(success=True, chunk_count=1),
    )

    user = signup(client)
    chat = _create_chat(client, user)
    _upload(client, user, chat["id"], filename="chat-only.txt")
    _upload_library(client, user, filename="library-only.txt")

    chat_listing = client.get(f"/api/chats/{chat['id']}/documents", headers=auth_headers(user))
    assert [d["filename"] for d in chat_listing.json()] == ["chat-only.txt"]

    library_listing = client.get("/api/documents", headers=auth_headers(user))
    assert [d["filename"] for d in library_listing.json()] == ["library-only.txt"]


def test_library_documents_are_scoped_to_their_uploader(client, monkeypatch):
    monkeypatch.setattr(documents_module, "azure_ai_configured", lambda: True)
    monkeypatch.setattr(documents_module, "upload_blob", _fake_upload_blob({}))
    monkeypatch.setattr(
        documents_module,
        "ingest_document",
        lambda **kwargs: IngestionResult(success=True, chunk_count=1),
    )

    owner = signup(client)
    other_user = signup(client)
    _upload_library(client, owner, filename="owner-only.txt")

    # A second user's library listing must never show the first user's
    # document - the only isolation boundary for a library upload, since
    # there's no chat/ownership check possible (there's no chat at all).
    other_listing = client.get("/api/documents", headers=auth_headers(other_user))
    assert other_listing.json() == []

    owner_listing = client.get("/api/documents", headers=auth_headers(owner))
    assert [d["filename"] for d in owner_listing.json()] == ["owner-only.txt"]


def test_delete_library_document_404s_for_another_user(client, monkeypatch):
    monkeypatch.setattr(documents_module, "azure_ai_configured", lambda: True)
    monkeypatch.setattr(documents_module, "upload_blob", _fake_upload_blob({}))
    monkeypatch.setattr(
        documents_module,
        "ingest_document",
        lambda **kwargs: IngestionResult(success=True, chunk_count=1),
    )
    monkeypatch.setattr(documents_module, "delete_document_vectors", lambda document_id: None)

    owner = signup(client)
    intruder = signup(client)
    doc = _upload_library(client, owner).json()

    response = client.delete(f"/api/documents/{doc['id']}", headers=auth_headers(intruder))
    assert response.status_code == 404

    # Still there for the actual owner afterward.
    owner_listing = client.get("/api/documents", headers=auth_headers(owner))
    assert len(owner_listing.json()) == 1


def test_delete_library_document_succeeds_for_its_owner(client, monkeypatch):
    monkeypatch.setattr(documents_module, "azure_ai_configured", lambda: True)
    monkeypatch.setattr(documents_module, "upload_blob", _fake_upload_blob({}))
    monkeypatch.setattr(
        documents_module,
        "ingest_document",
        lambda **kwargs: IngestionResult(success=True, chunk_count=1),
    )
    monkeypatch.setattr(documents_module, "delete_document_vectors", lambda document_id: None)

    owner = signup(client)
    doc = _upload_library(client, owner).json()

    response = client.delete(f"/api/documents/{doc['id']}", headers=auth_headers(owner))
    assert response.status_code == 204

    listing = client.get("/api/documents", headers=auth_headers(owner))
    assert listing.json() == []


def test_a_chat_scoped_document_never_appears_in_the_library_listing(client, monkeypatch):
    # A document uploaded to a specific chat has chat_id set - it must
    # never show up in the account-level library listing, which only
    # returns chat_id IS NULL rows.
    monkeypatch.setattr(documents_module, "azure_ai_configured", lambda: True)
    monkeypatch.setattr(documents_module, "upload_blob", _fake_upload_blob({}))
    monkeypatch.setattr(
        documents_module,
        "ingest_document",
        lambda **kwargs: IngestionResult(success=True, chunk_count=1),
    )

    user = signup(client)
    chat = _create_chat(client, user)
    _upload(client, user, chat["id"], filename="chat-scoped.txt")

    library_listing = client.get("/api/documents", headers=auth_headers(user))
    assert library_listing.json() == []
