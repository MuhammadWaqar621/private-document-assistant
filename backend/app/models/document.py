"""Document model - an uploaded file, plus its ingestion status.

A Document always belongs to exactly one user (`user_id`, never
optional - the non-negotiable isolation boundary). `chat_id` is optional:
most documents are uploaded within one Chat (via
POST /api/chats/{chat_id}/documents) and `chat_id` records that, but a
document can also be uploaded to the user's account-level "library" (via
POST /api/documents, no chat involved) with `chat_id=None` - those are
automatically searchable from every chat the user owns (the default
scope="all" retrieval never filters on chat_id at all - see
app/engine/qdrant_client.py's search()), while still being invisible to
anyone else. A `chat_id="chat"`-scoped search (the "only search this
chat's documents" checkbox) only matches an exact chat_id, so library
documents are excluded from that narrower mode - they aren't "this
chat's" uploads.

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
    # Nullable: NULL means an account-level "library" document (not tied to
    # any chat) - see the module docstring above. A NULL chat_id is never
    # matched by ON DELETE CASCADE, so deleting a chat can never delete a
    # library document.
    chat_id = Column(Integer, ForeignKey("chats.id", ondelete="CASCADE"), nullable=True, index=True)
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
