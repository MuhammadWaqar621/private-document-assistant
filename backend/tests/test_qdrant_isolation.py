"""
Tests for engine/qdrant_client.py's search() isolation boundary - the
single most important property in this project.

These run against a REAL Qdrant instance (the same one docker-compose
starts for the app - QDRANT_URL, defaulting to the docker-compose service
name "qdrant" so this also works when the suite is run inside the backend
container; override with QDRANT_TEST_URL if running the tests from the
host instead, e.g. QDRANT_TEST_URL=http://localhost:6333), using a
disposable, uniquely-named collection created and dropped per test - never
the real `querynest_documents` collection production documents live in.

Every test upserts small fake points (short synthetic embedding vectors,
not real Azure OpenAI embeddings - the multi-tenant filter behavior being
tested here doesn't depend on embedding content, only on payload
filtering) directly via `upsert_chunks`/`search`, using their `client=`
override parameter to target the disposable test collection instead of
the module-level default.
"""

import os
import uuid

import pytest
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from app.engine import qdrant_client as qc
from app.engine.qdrant_client import ChunkWithEmbedding

VECTOR_SIZE = 8


def _vec(seed: int) -> list[float]:
    """A small deterministic "embedding" - distinct per seed, but the
    isolation filter under test doesn't care about vector similarity, only
    about payload (user_id/chat_id) matching, so any fixed-length vector
    works here."""
    return [float((seed + i) % 7) / 7.0 for i in range(VECTOR_SIZE)]


@pytest.fixture()
def qdrant_url() -> str:
    return os.getenv("QDRANT_TEST_URL") or os.getenv("QDRANT_URL") or "http://qdrant:6333"


@pytest.fixture()
def isolated_collection(qdrant_url, monkeypatch):
    """Create a uniquely-named Qdrant collection for one test, point
    engine.qdrant_client.COLLECTION_NAME at it (so upsert_chunks()'s
    internal ensure_collection() call is a no-op against a collection that
    already exists with our test vector size), and drop it afterwards."""
    client = QdrantClient(url=qdrant_url)
    collection_name = f"test_isolation_{uuid.uuid4().hex[:10]}"

    client.create_collection(
        collection_name=collection_name,
        vectors_config=qmodels.VectorParams(size=VECTOR_SIZE, distance=qmodels.Distance.COSINE),
    )
    monkeypatch.setattr(qc, "COLLECTION_NAME", collection_name)

    try:
        yield client
    finally:
        client.delete_collection(collection_name)


# --- (a) user_id mismatch always returns 0 results, regardless of scope ----


def test_wrong_user_id_returns_nothing_in_all_scope(isolated_collection):
    client = isolated_collection
    qc.upsert_chunks(
        document_id=1,
        user_id=1,
        chat_id=100,
        filename="owner-only.pdf",
        chunks_with_embeddings=[
            ChunkWithEmbedding(chunk_index=0, page_number=1, text="secret", embedding=_vec(1))
        ],
        client=client,
    )

    results = qc.search(_vec(1), user_id=999, chat_id=None, top_k=10, client=client)

    assert results == []


def test_wrong_user_id_returns_nothing_even_when_chat_id_matches(isolated_collection):
    """A malicious/buggy caller who somehow guesses the right chat_id must
    still get nothing back if the user_id doesn't match - chat_id alone is
    never sufficient. This is the crux of the isolation guarantee."""
    client = isolated_collection
    qc.upsert_chunks(
        document_id=1,
        user_id=1,
        chat_id=100,
        filename="owner-only.pdf",
        chunks_with_embeddings=[
            ChunkWithEmbedding(chunk_index=0, page_number=1, text="secret", embedding=_vec(1))
        ],
        client=client,
    )

    # Same chat_id (100) as the real owner, but a different user_id.
    results = qc.search(_vec(1), user_id=2, chat_id=100, top_k=10, client=client)

    assert results == []


def test_two_users_reusing_the_same_chat_id_stay_isolated(isolated_collection):
    """chat_id is just an integer, not a global identifier - two different
    users can (and in this schema's foreign-key design, routinely do) have
    a chat with the same numeric id. The user_id filter must still keep
    their documents apart."""
    client = isolated_collection
    qc.upsert_chunks(
        document_id=1,
        user_id=1,
        chat_id=5,
        filename="user1-doc.pdf",
        chunks_with_embeddings=[
            ChunkWithEmbedding(chunk_index=0, page_number=1, text="user1 content", embedding=_vec(1))
        ],
        client=client,
    )
    qc.upsert_chunks(
        document_id=2,
        user_id=2,
        chat_id=5,
        filename="user2-doc.pdf",
        chunks_with_embeddings=[
            ChunkWithEmbedding(chunk_index=0, page_number=1, text="user2 content", embedding=_vec(2))
        ],
        client=client,
    )

    user1_results = qc.search(_vec(1), user_id=1, chat_id=5, top_k=10, client=client)
    user2_results = qc.search(_vec(2), user_id=2, chat_id=5, top_k=10, client=client)

    assert [r.filename for r in user1_results] == ["user1-doc.pdf"]
    assert [r.filename for r in user2_results] == ["user2-doc.pdf"]


# --- (b) chat_id=None (default "all" scope) spans every chat a user owns --


def test_default_scope_spans_multiple_chats_for_the_same_user(isolated_collection):
    client = isolated_collection
    qc.upsert_chunks(
        document_id=1,
        user_id=1,
        chat_id=10,
        filename="chat-a-doc.pdf",
        chunks_with_embeddings=[
            ChunkWithEmbedding(chunk_index=0, page_number=1, text="from chat A", embedding=_vec(1))
        ],
        client=client,
    )
    qc.upsert_chunks(
        document_id=2,
        user_id=1,
        chat_id=20,
        filename="chat-b-doc.pdf",
        chunks_with_embeddings=[
            ChunkWithEmbedding(chunk_index=0, page_number=1, text="from chat B", embedding=_vec(2))
        ],
        client=client,
    )

    results = qc.search(_vec(1), user_id=1, chat_id=None, top_k=10, client=client)

    filenames = {r.filename for r in results}
    assert filenames == {"chat-a-doc.pdf", "chat-b-doc.pdf"}


def test_default_scope_still_excludes_other_users(isolated_collection):
    client = isolated_collection
    qc.upsert_chunks(
        document_id=1,
        user_id=1,
        chat_id=10,
        filename="mine.pdf",
        chunks_with_embeddings=[
            ChunkWithEmbedding(chunk_index=0, page_number=1, text="mine", embedding=_vec(1))
        ],
        client=client,
    )
    qc.upsert_chunks(
        document_id=2,
        user_id=2,
        chat_id=10,
        filename="not-mine.pdf",
        chunks_with_embeddings=[
            ChunkWithEmbedding(chunk_index=0, page_number=1, text="not mine", embedding=_vec(2))
        ],
        client=client,
    )

    results = qc.search(_vec(1), user_id=1, chat_id=None, top_k=10, client=client)

    assert [r.filename for r in results] == ["mine.pdf"]


# --- (b2) chat_id=None points ("library" documents) are found by the ------
# default scope alongside every chat, but excluded once a search narrows
# to one specific chat - see app/models/document.py's module docstring.


def test_library_document_is_found_in_default_scope_alongside_a_chat_document(isolated_collection):
    client = isolated_collection
    qc.upsert_chunks(
        document_id=1,
        user_id=1,
        chat_id=10,
        filename="chat-doc.pdf",
        chunks_with_embeddings=[
            ChunkWithEmbedding(chunk_index=0, page_number=1, text="from a chat", embedding=_vec(1))
        ],
        client=client,
    )
    qc.upsert_chunks(
        document_id=2,
        user_id=1,
        chat_id=None,
        filename="library-doc.pdf",
        chunks_with_embeddings=[
            ChunkWithEmbedding(chunk_index=0, page_number=1, text="from the library", embedding=_vec(2))
        ],
        client=client,
    )

    results = qc.search(_vec(1), user_id=1, chat_id=None, top_k=10, client=client)

    filenames = {r.filename for r in results}
    assert filenames == {"chat-doc.pdf", "library-doc.pdf"}


def test_library_document_is_excluded_once_a_search_narrows_to_one_chat(isolated_collection):
    client = isolated_collection
    qc.upsert_chunks(
        document_id=1,
        user_id=1,
        chat_id=10,
        filename="chat-doc.pdf",
        chunks_with_embeddings=[
            ChunkWithEmbedding(chunk_index=0, page_number=1, text="from a chat", embedding=_vec(1))
        ],
        client=client,
    )
    qc.upsert_chunks(
        document_id=2,
        user_id=1,
        chat_id=None,
        filename="library-doc.pdf",
        chunks_with_embeddings=[
            ChunkWithEmbedding(chunk_index=0, page_number=1, text="from the library", embedding=_vec(2))
        ],
        client=client,
    )

    # Scoped to chat 10 specifically - the library document (chat_id=None)
    # must never surface here, even though nothing else narrows it out.
    results = qc.search(_vec(1), user_id=1, chat_id=10, top_k=10, client=client)

    assert [r.filename for r in results] == ["chat-doc.pdf"]


def test_library_document_still_respects_user_id_isolation(isolated_collection):
    client = isolated_collection
    qc.upsert_chunks(
        document_id=1,
        user_id=1,
        chat_id=None,
        filename="mine.pdf",
        chunks_with_embeddings=[
            ChunkWithEmbedding(chunk_index=0, page_number=1, text="mine", embedding=_vec(1))
        ],
        client=client,
    )
    qc.upsert_chunks(
        document_id=2,
        user_id=2,
        chat_id=None,
        filename="not-mine.pdf",
        chunks_with_embeddings=[
            ChunkWithEmbedding(chunk_index=0, page_number=1, text="not mine", embedding=_vec(2))
        ],
        client=client,
    )

    results = qc.search(_vec(1), user_id=1, chat_id=None, top_k=10, client=client)

    assert [r.filename for r in results] == ["mine.pdf"]


# --- (c) chat_id set ("chat" scope) restricts to that one chat only ------


def test_explicit_chat_id_restricts_to_that_chat_only(isolated_collection):
    client = isolated_collection
    qc.upsert_chunks(
        document_id=1,
        user_id=1,
        chat_id=10,
        filename="chat-a-doc.pdf",
        chunks_with_embeddings=[
            ChunkWithEmbedding(chunk_index=0, page_number=1, text="from chat A", embedding=_vec(1))
        ],
        client=client,
    )
    qc.upsert_chunks(
        document_id=2,
        user_id=1,
        chat_id=20,
        filename="chat-b-doc.pdf",
        chunks_with_embeddings=[
            ChunkWithEmbedding(chunk_index=0, page_number=1, text="from chat B", embedding=_vec(2))
        ],
        client=client,
    )

    results_a = qc.search(_vec(1), user_id=1, chat_id=10, top_k=10, client=client)
    results_b = qc.search(_vec(2), user_id=1, chat_id=20, top_k=10, client=client)

    assert [r.filename for r in results_a] == ["chat-a-doc.pdf"]
    assert [r.filename for r in results_b] == ["chat-b-doc.pdf"]

    # Query with chat B's own embedding (the closest possible match to
    # chat-b-doc.pdf) but scope the search to chat A's id - proves the
    # chat_id filter, not vector similarity, decides what's eligible: even
    # though chat-b-doc.pdf is the nearer vector, it must never surface
    # when the search is scoped to a different chat.
    cross_chat = qc.search(_vec(2), user_id=1, chat_id=10, top_k=10, client=client)
    assert [r.filename for r in cross_chat] == ["chat-a-doc.pdf"]


def test_search_result_fields_reflect_the_stored_payload(isolated_collection):
    """Sanity check on the SearchResult shape returned to app/engine/rag.py
    - page_number/filename/document_id must round-trip from the payload
    written by upsert_chunks, not just the isolation filter itself."""
    client = isolated_collection
    qc.upsert_chunks(
        document_id=42,
        user_id=7,
        chat_id=3,
        filename="report.pdf",
        chunks_with_embeddings=[
            ChunkWithEmbedding(
                chunk_index=0, page_number=5, text="quarterly figures", embedding=_vec(1)
            )
        ],
        client=client,
    )

    [result] = qc.search(_vec(1), user_id=7, chat_id=3, top_k=10, client=client)

    assert result.document_id == 42
    assert result.filename == "report.pdf"
    assert result.page_number == 5
    assert result.text == "quarterly figures"


def test_delete_document_removes_its_points_only(isolated_collection):
    client = isolated_collection
    qc.upsert_chunks(
        document_id=1,
        user_id=1,
        chat_id=10,
        filename="keep.pdf",
        chunks_with_embeddings=[
            ChunkWithEmbedding(chunk_index=0, page_number=1, text="keep me", embedding=_vec(1))
        ],
        client=client,
    )
    qc.upsert_chunks(
        document_id=2,
        user_id=1,
        chat_id=10,
        filename="delete.pdf",
        chunks_with_embeddings=[
            ChunkWithEmbedding(chunk_index=0, page_number=1, text="delete me", embedding=_vec(2))
        ],
        client=client,
    )

    qc.delete_document(2, client=client)

    remaining = qc.search(_vec(1), user_id=1, chat_id=None, top_k=10, client=client)
    assert [r.filename for r in remaining] == ["keep.pdf"]
