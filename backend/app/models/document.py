"""Document model - an uploaded file within a Chat, plus its ingestion status.

A Document is scoped to exactly one Chat (and, transitively, one user) -
this is the relational half of the per-chat isolation the RAG retrieval
depends on; the other half is the Qdrant payload filter in
app/engine/qdrant_client.py (every point also carries user_id + chat_id).

Raw file bytes are NOT stored in Postgres - they live on local disk at
storage/{user_id}/{document_id}/original.<ext> (see app/api/documents.py);
`storage_path` records where, so the file can be re-read or deleted later.

Ingestion (extract -> chunk -> embed -> upsert into Qdrant, via
app/engine/ingestion.py) runs synchronously inside the upload request for
this project's portfolio scope - `status` starts at "processing" and is
set to "ready" or "failed" (+ `error_message`) before the response is
returned. A production deployment would instead enqueue this onto a
background worker (Celery/RQ/arq) and let the client poll/subscribe for
status - see README.md's "Synchronous ingestion" note.
"""

import enum
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.db.base_class import Base


class DocumentStatus(str, enum.Enum):
    processing = "processing"
    ready = "ready"
    failed = "failed"


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    chat_id = Column(Integer, ForeignKey("chats.id", ondelete="CASCADE"), nullable=False, index=True)
    filename = Column(String, nullable=False)
    storage_path = Column(String, nullable=False)
    status = Column(
        Enum(DocumentStatus, name="document_status"),
        nullable=False,
        default=DocumentStatus.processing,
    )
    error_message = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    user = relationship("User")
    chat = relationship("Chat", back_populates="documents")
