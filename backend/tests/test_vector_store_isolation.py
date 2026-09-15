"""
Tests for engine/vector_store.py's search() isolation boundary - the
single most important property in this project.

These run against a REAL Postgres instance with the `vector` extension
available (pgvector) - VECTOR_TEST_DATABASE_URL if set, else DATABASE_URL,
else the docker-compose service default (postgres:5432 - now
pgvector/pgvector:pg16, see docker-compose.yml). Override with
VECTOR_TEST_DATABASE_URL if running the tests from the host against a
locally published port, e.g.
VECTOR_TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/querynest
(mirrors the old test_qdrant_isolation.py's QDRANT_TEST_URL pattern).

Every test creates a disposable, uniquely-named table (same shape as the
real `document_chunks` table, via vector_store.build_table()) and drops it
afterwards - never the real table production documents live in. Small
synthetic embedding vectors are used throughout (not real Azure OpenAI
embeddings) - the multi-tenant filter behavior under test doesn't depend
on embedding content, only on row filtering (user_id/chat_id).
"""

import os
import uuid

import pytest
import sqlalchemy as sa

from app.engine import vector_store as vs
from app.engine.vector_store import ChunkWithEmbedding

VECTOR_SIZE = 8


def _vec(seed: int) -> list[float]:
    """A small deterministic "embedding" - distinct per seed, but the
    isolation filter under test doesn't care about vector similarity, only
    about row (user_id/chat_id) matching, so any fixed-length vector works
    here."""
    return [float((seed + i) % 7) / 7.0 for i in range(VECTOR_SIZE)]


@pytest.fixture()
def pg_url() -> str:
    return (
        os.getenv("VECTOR_TEST_DATABASE_URL")
        or os.getenv("DATABASE_URL")
        or "postgresql://postgres:postgres@postgres:5432/querynest"
    )


@pytest.fixture()
def isolated_table(pg_url):
    """Create a uniquely-named, disposable document_chunks-shaped table
    for one test (ensuring the `vector` extension exists first) and drop
    it afterwards - mirrors the old Qdrant tests' isolated_collection
    fixture, one level down (a Postgres table instead of a Qdrant
    collection)."""
    engine = sa.create_engine(pg_url)
    table_name = f"test_vs_{uuid.uuid4().hex[:10]}"
    metadata = sa.MetaData()
    table = vs.build_table(metadata, table_name, dimensions=VECTOR_SIZE)

    try:
        with engine.begin() as conn:
            conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
    except Exception as exc:  # noqa: BLE001
        engine.dispose()
        pytest.skip(f"No pgvector-enabled Postgres reachable at {pg_url!r}: {exc}")

    metadata.create_all(engine)

    try:
        yield engine, table
    finally:
        metadata.drop_all(engine)
        engine.dispose()


def _upsert(engine, table, **kwargs):
    return vs.upsert_chunks(engine=engine, table=table, **kwargs)


def _search(engine, table, *args, **kwargs):
    return vs.search(*args, engine=engine, table=table, **kwargs)


# --- (a) user_id mismatch always returns 0 results, regardless of scope ----


def test_wrong_user_id_returns_nothing_in_all_scope(isolated_table):
    engine, table = isolated_table
    _upsert(
        engine,
        table,
        document_id=1,
        user_id=1,
        chat_id=100,
        filename="owner-only.pdf",
        chunks_with_embeddings=[
            ChunkWithEmbedding(chunk_index=0, page_number=1, text="secret", embedding=_vec(1))
        ],
    )

    results = _search(engine, table, _vec(1), user_id=999, chat_id=None, top_k=10)

    assert results == []


def test_wrong_user_id_returns_nothing_even_when_chat_id_matches(isolated_table):
    """A malicious/buggy caller who somehow guesses the right chat_id must
    still get nothing back if the user_id doesn't match - chat_id alone is
    never sufficient. This is the crux of the isolation guarantee."""
    engine, table = isolated_table
    _upsert(
        engine,
        table,
        document_id=1,
        user_id=1,
        chat_id=100,
        filename="owner-only.pdf",
        chunks_with_embeddings=[
            ChunkWithEmbedding(chunk_index=0, page_number=1, text="secret", embedding=_vec(1))
        ],
    )

    # Same chat_id (100) as the real owner, but a different user_id.
    results = _search(engine, table, _vec(1), user_id=2, chat_id=100, top_k=10)

    assert results == []


def test_two_users_reusing_the_same_chat_id_stay_isolated(isolated_table):
    """chat_id is just an integer, not a global identifier - two different
    users can (and in this schema's foreign-key design, routinely do) have
    a chat with the same numeric id. The user_id filter must still keep
    their documents apart."""
    engine, table = isolated_table
    _upsert(
        engine,
        table,
        document_id=1,
        user_id=1,
        chat_id=5,
        filename="user1-doc.pdf",
        chunks_with_embeddings=[
            ChunkWithEmbedding(chunk_index=0, page_number=1, text="user1 content", embedding=_vec(1))
        ],
    )
    _upsert(
        engine,
        table,
        document_id=2,
        user_id=2,
        chat_id=5,
        filename="user2-doc.pdf",
        chunks_with_embeddings=[
            ChunkWithEmbedding(chunk_index=0, page_number=1, text="user2 content", embedding=_vec(2))
        ],
    )

    user1_results = _search(engine, table, _vec(1), user_id=1, chat_id=5, top_k=10)
    user2_results = _search(engine, table, _vec(2), user_id=2, chat_id=5, top_k=10)

    assert [r.filename for r in user1_results] == ["user1-doc.pdf"]
    assert [r.filename for r in user2_results] == ["user2-doc.pdf"]


# --- (b) chat_id=None (default "all" scope) spans every chat a user owns --


def test_default_scope_spans_multiple_chats_for_the_same_user(isolated_table):
    engine, table = isolated_table
    _upsert(
        engine,
        table,
        document_id=1,
        user_id=1,
        chat_id=10,
        filename="chat-a-doc.pdf",
        chunks_with_embeddings=[
            ChunkWithEmbedding(chunk_index=0, page_number=1, text="from chat A", embedding=_vec(1))
        ],
    )
    _upsert(
        engine,
        table,
        document_id=2,
        user_id=1,
        chat_id=20,
        filename="chat-b-doc.pdf",
        chunks_with_embeddings=[
            ChunkWithEmbedding(chunk_index=0, page_number=1, text="from chat B", embedding=_vec(2))
        ],
    )

    results = _search(engine, table, _vec(1), user_id=1, chat_id=None, top_k=10)

    filenames = {r.filename for r in results}
    assert filenames == {"chat-a-doc.pdf", "chat-b-doc.pdf"}


def test_default_scope_still_excludes_other_users(isolated_table):
    engine, table = isolated_table
    _upsert(
        engine,
        table,
        document_id=1,
        user_id=1,
        chat_id=10,
        filename="mine.pdf",
        chunks_with_embeddings=[
            ChunkWithEmbedding(chunk_index=0, page_number=1, text="mine", embedding=_vec(1))
        ],
    )
    _upsert(
        engine,
        table,
        document_id=2,
        user_id=2,
        chat_id=10,
        filename="not-mine.pdf",
        chunks_with_embeddings=[
            ChunkWithEmbedding(chunk_index=0, page_number=1, text="not mine", embedding=_vec(2))
        ],
    )

    results = _search(engine, table, _vec(1), user_id=1, chat_id=None, top_k=10)

    assert [r.filename for r in results] == ["mine.pdf"]


# --- (b2) chat_id=None rows ("library" documents) are found by the -------
# default scope alongside every chat, but excluded once a search narrows
# to one specific chat - see app/models/document.py's module docstring.


def test_library_document_is_found_in_default_scope_alongside_a_chat_document(isolated_table):
    engine, table = isolated_table
    _upsert(
        engine,
        table,
        document_id=1,
        user_id=1,
        chat_id=10,
        filename="chat-doc.pdf",
        chunks_with_embeddings=[
            ChunkWithEmbedding(chunk_index=0, page_number=1, text="from a chat", embedding=_vec(1))
        ],
    )
    _upsert(
        engine,
        table,
        document_id=2,
        user_id=1,
        chat_id=None,
        filename="library-doc.pdf",
        chunks_with_embeddings=[
            ChunkWithEmbedding(chunk_index=0, page_number=1, text="from the library", embedding=_vec(2))
        ],
    )

    results = _search(engine, table, _vec(1), user_id=1, chat_id=None, top_k=10)

    filenames = {r.filename for r in results}
    assert filenames == {"chat-doc.pdf", "library-doc.pdf"}


def test_library_document_is_excluded_once_a_search_narrows_to_one_chat(isolated_table):
    engine, table = isolated_table
    _upsert(
        engine,
        table,
        document_id=1,
        user_id=1,
        chat_id=10,
        filename="chat-doc.pdf",
        chunks_with_embeddings=[
            ChunkWithEmbedding(chunk_index=0, page_number=1, text="from a chat", embedding=_vec(1))
        ],
    )
    _upsert(
        engine,
        table,
        document_id=2,
        user_id=1,
        chat_id=None,
        filename="library-doc.pdf",
        chunks_with_embeddings=[
            ChunkWithEmbedding(chunk_index=0, page_number=1, text="from the library", embedding=_vec(2))
        ],
    )

    # Scoped to chat 10 specifically - the library document (chat_id=None)
    # must never surface here, even though nothing else narrows it out.
    results = _search(engine, table, _vec(1), user_id=1, chat_id=10, top_k=10)

    assert [r.filename for r in results] == ["chat-doc.pdf"]


def test_library_document_still_respects_user_id_isolation(isolated_table):
    engine, table = isolated_table
    _upsert(
        engine,
        table,
        document_id=1,
        user_id=1,
        chat_id=None,
        filename="mine.pdf",
        chunks_with_embeddings=[
            ChunkWithEmbedding(chunk_index=0, page_number=1, text="mine", embedding=_vec(1))
        ],
    )
    _upsert(
        engine,
        table,
        document_id=2,
        user_id=2,
        chat_id=None,
        filename="not-mine.pdf",
        chunks_with_embeddings=[
            ChunkWithEmbedding(chunk_index=0, page_number=1, text="not mine", embedding=_vec(2))
        ],
    )

    results = _search(engine, table, _vec(1), user_id=1, chat_id=None, top_k=10)

    assert [r.filename for r in results] == ["mine.pdf"]


# --- (c) chat_id set ("chat" scope) restricts to that one chat only ------


def test_explicit_chat_id_restricts_to_that_chat_only(isolated_table):
    engine, table = isolated_table
    _upsert(
        engine,
        table,
        document_id=1,
        user_id=1,
        chat_id=10,
        filename="chat-a-doc.pdf",
        chunks_with_embeddings=[
            ChunkWithEmbedding(chunk_index=0, page_number=1, text="from chat A", embedding=_vec(1))
        ],
    )
    _upsert(
        engine,
        table,
        document_id=2,
        user_id=1,
        chat_id=20,
        filename="chat-b-doc.pdf",
        chunks_with_embeddings=[
            ChunkWithEmbedding(chunk_index=0, page_number=1, text="from chat B", embedding=_vec(2))
        ],
    )

    results_a = _search(engine, table, _vec(1), user_id=1, chat_id=10, top_k=10)
    results_b = _search(engine, table, _vec(2), user_id=1, chat_id=20, top_k=10)

    assert [r.filename for r in results_a] == ["chat-a-doc.pdf"]
    assert [r.filename for r in results_b] == ["chat-b-doc.pdf"]

    # Query with chat B's own embedding (the closest possible match to
    # chat-b-doc.pdf) but scope the search to chat A's id - proves the
    # chat_id filter, not vector similarity, decides what's eligible: even
    # though chat-b-doc.pdf is the nearer vector, it must never surface
    # when the search is scoped to a different chat.
    cross_chat = _search(engine, table, _vec(2), user_id=1, chat_id=10, top_k=10)
    assert [r.filename for r in cross_chat] == ["chat-a-doc.pdf"]


def test_search_result_fields_reflect_the_stored_row(isolated_table):
    """Sanity check on the SearchResult shape returned to app/engine/rag.py
    - page_number/filename/document_id must round-trip from the row
    written by upsert_chunks, not just the isolation filter itself."""
    engine, table = isolated_table
    _upsert(
        engine,
        table,
        document_id=42,
        user_id=7,
        chat_id=3,
        filename="report.pdf",
        chunks_with_embeddings=[
            ChunkWithEmbedding(
                chunk_index=0, page_number=5, text="quarterly figures", embedding=_vec(1)
            )
        ],
    )

    [result] = _search(engine, table, _vec(1), user_id=7, chat_id=3, top_k=10)

    assert result.document_id == 42
    assert result.filename == "report.pdf"
    assert result.page_number == 5
    assert result.text == "quarterly figures"


def test_delete_document_removes_its_rows_only(isolated_table):
    engine, table = isolated_table
    _upsert(
        engine,
        table,
        document_id=1,
        user_id=1,
        chat_id=10,
        filename="keep.pdf",
        chunks_with_embeddings=[
            ChunkWithEmbedding(chunk_index=0, page_number=1, text="keep me", embedding=_vec(1))
        ],
    )
    _upsert(
        engine,
        table,
        document_id=2,
        user_id=1,
        chat_id=10,
        filename="delete.pdf",
        chunks_with_embeddings=[
            ChunkWithEmbedding(chunk_index=0, page_number=1, text="delete me", embedding=_vec(2))
        ],
    )

    vs.delete_document(2, engine=engine, table=table)

    remaining = _search(engine, table, _vec(1), user_id=1, chat_id=None, top_k=10)
    assert [r.filename for r in remaining] == ["keep.pdf"]


def test_reingesting_a_document_overwrites_its_previous_chunks(isolated_table):
    """upsert_chunks() must be an actual upsert (ON CONFLICT on
    (document_id, chunk_index)) - re-ingesting a document should replace
    its previous rows, not accumulate duplicates alongside them."""
    engine, table = isolated_table
    _upsert(
        engine,
        table,
        document_id=1,
        user_id=1,
        chat_id=10,
        filename="v1.pdf",
        chunks_with_embeddings=[
            ChunkWithEmbedding(chunk_index=0, page_number=1, text="old text", embedding=_vec(1))
        ],
    )
    _upsert(
        engine,
        table,
        document_id=1,
        user_id=1,
        chat_id=10,
        filename="v2.pdf",
        chunks_with_embeddings=[
            ChunkWithEmbedding(chunk_index=0, page_number=1, text="new text", embedding=_vec(1))
        ],
    )

    results = _search(engine, table, _vec(1), user_id=1, chat_id=None, top_k=10)
    assert len(results) == 1
    assert results[0].filename == "v2.pdf"
    assert results[0].text == "new text"


def test_score_threshold_excludes_dissimilar_rows(isolated_table):
    """A row whose embedding is far from the query vector must be excluded
    once a score_threshold is given - mirrors RAG_MIN_RELEVANCE_SCORE in
    app/engine/rag.py."""
    engine, table = isolated_table
    close_vec = _vec(1)
    far_vec = [-v for v in close_vec]  # maximally dissimilar (cosine distance ~2)

    _upsert(
        engine,
        table,
        document_id=1,
        user_id=1,
        chat_id=None,
        filename="close.pdf",
        chunks_with_embeddings=[
            ChunkWithEmbedding(chunk_index=0, page_number=1, text="close", embedding=close_vec)
        ],
    )
    _upsert(
        engine,
        table,
        document_id=2,
        user_id=1,
        chat_id=None,
        filename="far.pdf",
        chunks_with_embeddings=[
            ChunkWithEmbedding(chunk_index=0, page_number=1, text="far", embedding=far_vec)
        ],
    )

    results = _search(
        engine, table, close_vec, user_id=1, chat_id=None, top_k=10, score_threshold=0.75
    )

    assert [r.filename for r in results] == ["close.pdf"]
