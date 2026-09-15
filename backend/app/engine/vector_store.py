"""
Vector storage/search for document chunk embeddings, backed by pgvector on
the SAME Postgres database the rest of the app uses (app/db/session.py) -
replaces the old Qdrant-based engine/qdrant_client.py so the whole stack
runs on one Vercel Postgres database with no separate vector service.

Configuration is read directly from environment variables (DATABASE_URL -
the same variable app/db/session.py uses, plus AZURE_EM_DIMENSIONS via
azure_client.get_embedding_dimensions()) rather than importing
app.core.config, so this module keeps the same "zero dependency on the
rest of the app" contract the other engine/ modules follow - see
app/engine/__init__.py.

The `document_chunks` table (created by an Alembic migration - see
backend/alembic/versions/, NOT auto-created here, since a Vercel
serverless function has no business running DDL at import time) has one
row per chunk, with an `embedding vector(AZURE_EM_DIMENSIONS)` column. A
unique constraint on (document_id, chunk_index) is what makes
upsert_chunks() an actual upsert (re-ingesting a document overwrites its
previous rows instead of accumulating duplicates), mirroring the
deterministic-point-id trick the old Qdrant client used.

MULTI-TENANT ISOLATION: every row carries `user_id` and `chat_id`.
search()'s `user_id` filter is ALWAYS applied and is non-negotiable - a
chunk belonging to one user is never retrievable by another, regardless of
scope. `chat_id` is OPTIONAL: leaving it None (the default) searches every
chat the user owns (the product's default "search everything I've
uploaded" behavior); passing an explicit chat_id narrows retrieval to just
that chat's uploads. See the README's "Document retrieval scope" section
and test_vector_store_isolation.py, which exercises this against a real
pgvector-enabled Postgres instance.
"""

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import List, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    and_,
    delete as sa_delete,
    select,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine, create_engine

from app.engine.azure_client import get_embedding_dimensions

TABLE_NAME = os.getenv("VECTOR_TABLE", "document_chunks")


def build_table(metadata: MetaData, name: str, dimensions: Optional[int] = None) -> Table:
    """Define the document-chunks table shape against a caller-supplied
    MetaData/name - used both for the module-level default table below and
    (in tests) for a disposable, uniquely-named table so tests never touch
    the real `document_chunks` table. The embedding column's size comes
    from AZURE_EM_DIMENSIONS (default 1536), matching the Alembic
    migration that actually creates this table in Postgres. The unique
    constraint on (document_id, chunk_index) is what makes upsert_chunks()
    an actual upsert via ON CONFLICT."""
    return Table(
        name,
        metadata,
        Column("id", Integer, primary_key=True),
        Column("document_id", Integer, nullable=False, index=True),
        Column("user_id", Integer, nullable=False, index=True),
        Column("chat_id", Integer, nullable=True, index=True),
        Column("filename", String, nullable=False),
        Column("page_number", Integer, nullable=False),
        Column("chunk_index", Integer, nullable=False),
        Column("text", Text, nullable=False),
        Column("embedding", Vector(dimensions or get_embedding_dimensions()), nullable=False),
        UniqueConstraint("document_id", "chunk_index", name=f"uq_{name}_document_id_chunk_index"),
    )


_metadata = MetaData()
document_chunks = build_table(_metadata, TABLE_NAME)


@dataclass(frozen=True)
class ChunkWithEmbedding:
    chunk_index: int
    page_number: int
    text: str
    embedding: List[float]


@dataclass(frozen=True)
class SearchResult:
    text: str
    page_number: int
    filename: str
    document_id: int
    score: float


@lru_cache
def get_engine() -> Engine:
    url = (os.getenv("DATABASE_URL") or "postgresql://postgres:postgres@postgres:5432/querynest").strip()
    return create_engine(url, pool_pre_ping=True)


def upsert_chunks(
    document_id: int,
    user_id: int,
    chat_id: Optional[int],
    filename: str,
    chunks_with_embeddings: List[ChunkWithEmbedding],
    engine: Optional[Engine] = None,
    table: Table = document_chunks,
) -> int:
    """Upsert one document's chunks as rows in `table`. Returns the number
    of rows written. `chat_id=None` (an account-level "library" document -
    see app/models/document.py) is stored as-is; search()'s chat_id filter
    is only applied when the caller passes an explicit chat_id, so a
    library row is still found by the default scope="all" search but
    excluded from a scope="chat" search. Re-ingesting the same
    (document_id, chunk_index) pair overwrites the existing row via
    ON CONFLICT, rather than accumulating duplicates."""
    if not chunks_with_embeddings:
        return 0

    engine = engine or get_engine()
    rows = [
        {
            "document_id": document_id,
            "user_id": user_id,
            "chat_id": chat_id,
            "filename": filename,
            "page_number": chunk.page_number,
            "chunk_index": chunk.chunk_index,
            "text": chunk.text,
            "embedding": chunk.embedding,
        }
        for chunk in chunks_with_embeddings
    ]

    stmt = pg_insert(table).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[table.c.document_id, table.c.chunk_index],
        set_={
            "user_id": stmt.excluded.user_id,
            "chat_id": stmt.excluded.chat_id,
            "filename": stmt.excluded.filename,
            "page_number": stmt.excluded.page_number,
            "text": stmt.excluded.text,
            "embedding": stmt.excluded.embedding,
        },
    )

    with engine.begin() as conn:
        conn.execute(stmt)
    return len(rows)


def delete_document(document_id: int, engine: Optional[Engine] = None, table: Table = document_chunks) -> None:
    """Delete every row belonging to a document (used when a Document row
    is deleted via app/api/documents.py)."""
    engine = engine or get_engine()
    with engine.begin() as conn:
        conn.execute(sa_delete(table).where(table.c.document_id == document_id))


def search(
    query_embedding: List[float],
    user_id: int,
    chat_id: Optional[int] = None,
    top_k: int = 5,
    score_threshold: Optional[float] = None,
    engine: Optional[Engine] = None,
    table: Table = document_chunks,
) -> List[SearchResult]:
    """Vector search scoped to `user_id` (always) and, optionally,
    `chat_id`, ordered by pgvector cosine distance (nearest first).

    `user_id` is a REQUIRED filter condition every time - a chunk stored
    under a different user_id is never returned here, no matter how
    similar its embedding is to the query. This is the non-negotiable
    multi-tenant isolation boundary between users.

    `chat_id` is OPTIONAL. Leave it `None` (the default) to search across
    every chat the user owns; pass an explicit `chat_id` to additionally
    restrict results to that one chat's uploads.

    `score_threshold` is OPTIONAL (cosine similarity, 0-1) - when given, it
    is applied as part of the query itself (not a post-hoc filter over the
    top_k nearest rows), so up to `top_k` results are returned from among
    every row that meets the threshold - the same semantics
    app/engine/rag.py relies on via RAG_MIN_RELEVANCE_SCORE."""
    engine = engine or get_engine()

    distance = table.c.embedding.cosine_distance(query_embedding)

    conditions = [table.c.user_id == user_id]
    if chat_id is not None:
        conditions.append(table.c.chat_id == chat_id)
    if score_threshold is not None:
        # pgvector cosine_distance = 1 - cosine_similarity.
        conditions.append(distance <= (1 - score_threshold))

    stmt = (
        select(
            table.c.text,
            table.c.page_number,
            table.c.filename,
            table.c.document_id,
            distance.label("distance"),
        )
        .where(and_(*conditions))
        .order_by(distance)
        .limit(top_k)
    )

    with engine.connect() as conn:
        rows = conn.execute(stmt).all()

    return [
        SearchResult(
            text=row.text,
            page_number=row.page_number,
            filename=row.filename,
            document_id=row.document_id,
            score=1.0 - row.distance,
        )
        for row in rows
    ]
