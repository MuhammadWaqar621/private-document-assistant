"""
Orchestrates document ingestion: extract -> chunk -> embed -> upsert.

No database writes happen here - this module only returns plain data
describing success/failure (IngestionResult). The API layer
(app/api/documents.py) is responsible for creating/updating the Document
row's status from that result.
"""

from dataclasses import dataclass
from typing import List, Optional

from app.engine.azure_client import (
    azure_ai_configured,
    get_embedding_client,
    get_embedding_config,
)
from app.engine.chunking import Chunk, chunk_pages
from app.engine.extraction import UnsupportedFileTypeError, extract_pages
from app.engine.qdrant_client import ChunkWithEmbedding, upsert_chunks

EMBEDDING_BATCH_SIZE = 16


@dataclass(frozen=True)
class IngestionResult:
    success: bool
    chunk_count: int = 0
    error_message: Optional[str] = None


def ingest_document(
    raw_bytes: bytes,
    filename: str,
    document_id: int,
    user_id: int,
    chat_id: int,
) -> IngestionResult:
    """Run the full pipeline for one document. Never raises - any failure
    is captured in the returned IngestionResult so the caller can persist
    a Document.status of "failed" + error_message instead of crashing the
    request."""
    if not azure_ai_configured():
        return IngestionResult(success=False, error_message="Azure OpenAI is not configured.")

    try:
        pages = extract_pages(raw_bytes, filename)
    except UnsupportedFileTypeError as exc:
        return IngestionResult(success=False, error_message=str(exc))
    except Exception as exc:  # noqa: BLE001 - any extraction failure -> failed status, not a crash
        return IngestionResult(success=False, error_message=f"Failed to read document: {exc}")

    chunks = chunk_pages(pages)
    if not chunks:
        return IngestionResult(
            success=False, error_message="No extractable text found in document."
        )

    try:
        embedded = _embed_chunks(chunks)
    except Exception as exc:  # noqa: BLE001
        return IngestionResult(success=False, error_message=f"Embedding failed: {exc}")

    try:
        count = upsert_chunks(
            document_id=document_id,
            user_id=user_id,
            chat_id=chat_id,
            filename=filename,
            chunks_with_embeddings=embedded,
        )
    except Exception as exc:  # noqa: BLE001
        return IngestionResult(success=False, error_message=f"Failed to store embeddings: {exc}")

    return IngestionResult(success=True, chunk_count=count)


def _embed_chunks(chunks: List[Chunk]) -> List[ChunkWithEmbedding]:
    client = get_embedding_client()
    config = get_embedding_config()
    assert config is not None  # already checked by azure_ai_configured()

    embedded: List[ChunkWithEmbedding] = []
    for start in range(0, len(chunks), EMBEDDING_BATCH_SIZE):
        batch = chunks[start : start + EMBEDDING_BATCH_SIZE]
        response = client.embeddings.create(model=config.model, input=[c.text for c in batch])
        for chunk, item in zip(batch, response.data):
            embedded.append(
                ChunkWithEmbedding(
                    chunk_index=chunk.chunk_index,
                    page_number=chunk.page_number,
                    text=chunk.text,
                    embedding=item.embedding,
                )
            )
    return embedded
