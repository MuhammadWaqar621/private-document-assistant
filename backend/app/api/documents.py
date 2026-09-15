"""
Document upload/list/delete endpoints - two routers, sharing the same
upload/ingest pipeline (`_save_and_ingest()` below):

- `router` (prefix /api/chats/{chat_id}/documents): a document scoped to
  one chat the current user owns - same ownership pattern as
  app/api/chats.py (404, not 403, for a chat that exists but belongs to
  someone else).
- `library_router` (prefix /api/documents): an account-level document, not
  tied to any chat (`chat_id=None`) - automatically searchable from every
  chat the user owns via the default scope="all" retrieval, without
  needing to attach it to a specific chat first. See
  app/models/document.py's module docstring for the isolation reasoning.

Both are one of the two places (with app/api/messages.py) that touch both
the DB/auth stack and app/engine/* - they check auth/ownership, upload the
raw bytes to Vercel Blob storage, call the plain engine ingestion function,
and persist the resulting status. Ingestion runs synchronously inside the
request for this portfolio project's scope: on success the Document row
becomes status=ready, on failure status=failed + error_message, but the
request itself never crashes either way. A production deployment would
instead hand this off to a background worker (Celery/RQ/arq) and let the
client poll for status - see README.md's "Synchronous ingestion" tradeoff.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.engine.azure_client import azure_ai_configured
from app.engine.blob_storage import BlobStorageError, delete_blob, upload_blob
from app.engine.ingestion import ingest_document
from app.engine.llm_provider import get_llm_provider_name
from app.engine.vector_store import delete_document as delete_document_vectors
from app.models import Chat, Document, DocumentStatus, User

router = APIRouter(prefix="/api/chats/{chat_id}/documents", tags=["documents"])
library_router = APIRouter(prefix="/api/documents", tags=["documents"])

# The blob pathname is chosen from this fixed allow-list, never taken
# verbatim from the client-supplied filename - a filename like
# "x.txt/../../../etc/whatever" would otherwise let its "extension"
# (everything after the last ".") inject path separators/".." segments
# into the storage path built below.
# jpg/jpeg/png (OCR via engine/extraction.py's EasyOCR path) added
# alongside the original pdf/docx/txt. Word support stays at modern .docx
# only - legacy binary .doc is out of scope (would need a separate
# toolchain such as antiword/LibreOffice headless conversion - see README).
ALLOWED_EXTENSIONS = {"pdf", "docx", "txt", "jpg", "jpeg", "png"}


# --- Schemas -----------------------------------------------------------------


class DocumentOut(BaseModel):
    id: int
    filename: str
    status: DocumentStatus
    error_message: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Helpers -------------------------------------------------------------


def _get_owned_chat(db: Session, chat_id: int, user: User) -> Chat:
    chat = db.get(Chat, chat_id)
    if chat is None or chat.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found")
    return chat


def _get_owned_document(db: Session, chat_id: int, document_id: int, user: User) -> Document:
    document = db.get(Document, document_id)
    if document is None or document.chat_id != chat_id or document.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return document


def _get_owned_library_document(db: Session, document_id: int, user: User) -> Document:
    document = db.get(Document, document_id)
    if document is None or document.chat_id is not None or document.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return document


def _azure_not_configured_error() -> HTTPException:
    # Error code kept as "azure_ai_not_configured" for backward
    # compatibility (see app/engine/azure_client.py's azure_ai_configured()
    # docstring); the message is provider-aware since the chat half may now
    # be Groq-backed (LLM_PROVIDER, default "groq" - see
    # app/engine/llm_provider.py).
    provider = get_llm_provider_name()
    chat_hint = "LLM_ENDPOINT*" if provider == "azure" else "GROQ_API_KEY"
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "error": "azure_ai_not_configured",
            "message": (
                "AI is not configured. Set AZURE_EM_* (embeddings) and "
                f"{chat_hint} (chat, provider={provider}) in .env."
            ),
        },
    )


# --- Shared upload/ingest pipeline -----------------------------------------


async def _save_and_ingest(
    db: Session, user: User, file: UploadFile, chat_id: Optional[int]
) -> Document:
    """Upload the raw bytes to Vercel Blob storage, create the Document
    row, and run ingestion synchronously - shared by both the chat-scoped
    and account-level library upload endpoints below. `chat_id=None` for a
    library upload; everything else (extraction, chunking, embedding,
    vector-store upsert, supported file types) is identical for both - a
    library document goes through the exact same app/engine/extraction.py
    pipeline (PDF, DOCX, TXT, and OCR'd images), it's just not associated
    with one chat.

    Ingestion itself (app/engine/extraction.py onward) operates entirely
    on the in-memory `raw_bytes` already read below - no local/temp file
    is needed for processing, only the original upload's own copy in Blob
    storage (for later re-download/deletion)."""
    if not azure_ai_configured():
        raise _azure_not_configured_error()

    filename = file.filename or "upload"
    raw_bytes = await file.read()

    document = Document(
        user_id=user.id,
        chat_id=chat_id,
        filename=filename,
        storage_path="",
        status=DocumentStatus.processing,
    )
    db.add(document)
    db.commit()
    db.refresh(document)

    raw_ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    ext = raw_ext if raw_ext in ALLOWED_EXTENSIONS else "bin"
    pathname = f"{user.id}/{document.id}/original.{ext}"

    try:
        uploaded = upload_blob(pathname, raw_bytes, content_type=file.content_type)
    except BlobStorageError as exc:
        document.status = DocumentStatus.failed
        document.error_message = f"Failed to store uploaded file: {exc}"
        db.commit()
        db.refresh(document)
        return document

    document.storage_path = uploaded.url
    db.commit()

    result = ingest_document(
        raw_bytes=raw_bytes,
        filename=filename,
        document_id=document.id,
        user_id=user.id,
        chat_id=chat_id,
    )

    if result.success:
        document.status = DocumentStatus.ready
        document.error_message = None
    else:
        document.status = DocumentStatus.failed
        document.error_message = result.error_message

    db.commit()
    db.refresh(document)
    return document


# --- Chat-scoped endpoints ---------------------------------------------------


@router.post("", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def upload_document(
    chat_id: int,
    file: UploadFile,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Document:
    _get_owned_chat(db, chat_id, current_user)
    return await _save_and_ingest(db, current_user, file, chat_id=chat_id)


@router.get("", response_model=list[DocumentOut])
def list_documents(
    chat_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[Document]:
    _get_owned_chat(db, chat_id, current_user)
    return (
        db.query(Document)
        .filter(Document.chat_id == chat_id, Document.user_id == current_user.id)
        .order_by(Document.created_at.desc())
        .all()
    )


def _delete_document(db: Session, document: Document) -> None:
    try:
        delete_document_vectors(document.id)
    except Exception:  # noqa: BLE001 - a vector-store hiccup shouldn't block deleting the DB row
        pass

    if document.storage_path:
        try:
            delete_blob(document.storage_path)
        except Exception:  # noqa: BLE001 - a Blob hiccup shouldn't block deleting the DB row
            pass

    db.delete(document)
    db.commit()


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    chat_id: int,
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    _get_owned_chat(db, chat_id, current_user)
    document = _get_owned_document(db, chat_id, document_id, current_user)
    _delete_document(db, document)


# --- Account-level "library" endpoints (not tied to any chat) ---------------
#
# Uploaded via POST /api/documents (no chat_id in the URL or payload) -
# automatically searchable from every chat the user owns via the default
# scope="all" retrieval (app/engine/rag.py's retrieve()/stream_agentic_reply()
# pass chat_id=None in that mode, and vector_store.search() only filters
# on chat_id when one is explicitly given), while remaining invisible to
# every other user, and excluded from a scope="chat"-narrowed search - see
# app/models/document.py's module docstring.


@library_router.post("", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def upload_library_document(
    file: UploadFile,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Document:
    return await _save_and_ingest(db, current_user, file, chat_id=None)


@library_router.get("", response_model=list[DocumentOut])
def list_library_documents(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[Document]:
    return (
        db.query(Document)
        .filter(Document.chat_id.is_(None), Document.user_id == current_user.id)
        .order_by(Document.created_at.desc())
        .all()
    )


@library_router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_library_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    document = _get_owned_library_document(db, document_id, current_user)
    _delete_document(db, document)
