"""
Thin wrapper around qdrant_client.QdrantClient.

Configuration is read directly from environment variables (QDRANT_URL,
optional QDRANT_API_KEY for Qdrant Cloud) - see azure_client.py's module
docstring for why this package avoids importing app.core.config.

MULTI-TENANT ISOLATION: every point upserted here carries a `user_id` and
`chat_id` payload field. `search()`'s `user_id` filter is ALWAYS applied
and is non-negotiable - a document belonging to one user is never
retrievable by another, regardless of scope. The `chat_id` filter is
OPTIONAL: by default (`chat_id=None`) retrieval draws from every chat the
user owns, so a question in chat A can be answered from a document
uploaded in chat B, as long as both belong to the same user - this is the
product's default "search everything I've uploaded" behavior. Passing an
explicit `chat_id` narrows retrieval to just that chat's uploads (the
frontend's "Only search this chat's documents" checkbox opts into this).
Do not weaken the `user_id` condition (e.g. to a `should` clause, or drop
it) without a very good reason - see the isolation tests exercising this
in the manual verification section of the README.
"""

import os
import uuid
from dataclasses import dataclass
from functools import lru_cache
from typing import List, Optional

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from app.engine.azure_client import get_embedding_dimensions

COLLECTION_NAME = os.getenv("QDRANT_COLLECTION", "querynest_documents")


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
def get_qdrant_client() -> QdrantClient:
    url = (os.getenv("QDRANT_URL") or "http://localhost:6333").strip()
    api_key = (os.getenv("QDRANT_API_KEY") or "").strip() or None
    return QdrantClient(url=url, api_key=api_key)


def ensure_collection(client: Optional[QdrantClient] = None) -> None:
    """Create the collection if it doesn't exist yet, sized for the
    configured embedding model (AZURE_EM_DIMENSIONS, default 1536)."""
    client = client or get_qdrant_client()
    existing = {c.name for c in client.get_collections().collections}
    if COLLECTION_NAME in existing:
        return
    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=qmodels.VectorParams(
            size=get_embedding_dimensions(),
            distance=qmodels.Distance.COSINE,
        ),
    )


def _point_id(document_id: int, chunk_index: int) -> str:
    # Deterministic UUID derived from (document_id, chunk_index), so
    # re-ingesting the same document overwrites its previous points
    # instead of accumulating duplicates.
    return str(uuid.uuid5(uuid.NAMESPACE_OID, f"document:{document_id}:chunk:{chunk_index}"))


def upsert_chunks(
    document_id: int,
    user_id: int,
    chat_id: int,
    filename: str,
    chunks_with_embeddings: List[ChunkWithEmbedding],
    client: Optional[QdrantClient] = None,
) -> int:
    """Upsert one document's chunks as Qdrant points. Returns the number of
    points written. Every point's payload carries user_id + chat_id, which
    is what search() below filters on for tenant isolation."""
    client = client or get_qdrant_client()
    ensure_collection(client)

    points = [
        qmodels.PointStruct(
            id=_point_id(document_id, chunk.chunk_index),
            vector=chunk.embedding,
            payload={
                "document_id": document_id,
                "user_id": user_id,
                "chat_id": chat_id,
                "filename": filename,
                "page_number": chunk.page_number,
                "chunk_index": chunk.chunk_index,
                "text": chunk.text,
            },
        )
        for chunk in chunks_with_embeddings
    ]
    if not points:
        return 0

    client.upsert(collection_name=COLLECTION_NAME, points=points)
    return len(points)


def delete_document(document_id: int, client: Optional[QdrantClient] = None) -> None:
    """Delete every point belonging to a document (used when a Document row
    is deleted via app/api/documents.py)."""
    client = client or get_qdrant_client()
    client.delete(
        collection_name=COLLECTION_NAME,
        points_selector=qmodels.FilterSelector(
            filter=qmodels.Filter(
                must=[
                    qmodels.FieldCondition(
                        key="document_id", match=qmodels.MatchValue(value=document_id)
                    )
                ]
            )
        ),
    )


def search(
    query_embedding: List[float],
    user_id: int,
    chat_id: Optional[int] = None,
    top_k: int = 5,
    score_threshold: Optional[float] = None,
    client: Optional[QdrantClient] = None,
) -> List[SearchResult]:
    """Vector search scoped to `user_id` (always) and, optionally, `chat_id`.

    `user_id` is a REQUIRED `must` filter condition every time - a chunk
    stored under a different user_id is never returned here, no matter how
    similar its embedding is to the query. This is the non-negotiable
    multi-tenant isolation boundary between users.

    `chat_id` is OPTIONAL. Leave it `None` (the default) to search across
    every chat the user owns - the product's default behavior, where a
    document uploaded in one chat can answer a question asked in another,
    as long as both are the same user's. Pass an explicit `chat_id` to
    additionally restrict results to that one chat's uploads (opt-in,
    narrower scope).

    `score_threshold` is OPTIONAL and off by default - Qdrant otherwise
    always returns its top-k nearest points regardless of how irrelevant
    they are. This wrapper stays a generic scoped-search primitive; RAG
    policy (what counts as "relevant enough") belongs in the caller - see
    app/engine/rag.py's retrieve(), which passes MIN_RELEVANCE_SCORE."""
    client = client or get_qdrant_client()

    must_conditions = [
        qmodels.FieldCondition(key="user_id", match=qmodels.MatchValue(value=user_id)),
    ]
    if chat_id is not None:
        must_conditions.append(
            qmodels.FieldCondition(key="chat_id", match=qmodels.MatchValue(value=chat_id))
        )
    query_filter = qmodels.Filter(must=must_conditions)

    hits = client.search(
        collection_name=COLLECTION_NAME,
        query_vector=query_embedding,
        query_filter=query_filter,
        limit=top_k,
        score_threshold=score_threshold,
    )

    return [
        SearchResult(
            text=hit.payload.get("text", "") if hit.payload else "",
            page_number=hit.payload.get("page_number", 0) if hit.payload else 0,
            filename=hit.payload.get("filename", "") if hit.payload else "",
            document_id=hit.payload.get("document_id", 0) if hit.payload else 0,
            score=hit.score,
        )
        for hit in hits
    ]
