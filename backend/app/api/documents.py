"""
Document upload/list/delete endpoints, scoped to a chat the current user
owns - same ownership pattern as app/api/chats.py (404, not 403, for a chat
that exists but belongs to someone else).

This is one of the two places (with app/api/messages.py) that touches both
the DB/auth stack and app/engine/* - it checks auth/ownership, saves the
raw upload to local disk, calls the plain engine ingestion function, and
persists the resulting status. Ingestion runs synchronously inside the
request for this portfolio project's scope: on success the Document row
becomes status=ready, on failure status=failed + error_message, but the
request itself never crashes either way. A production deployment would
instead hand this off to a background worker (Celery/RQ/arq) and let the
client poll for status - see README.md's "Synchronous ingestion" tradeoff.
"""

import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.engine.azure_client import azure_ai_configured
from app.engine.ingestion import ingest_document
from app.engine.llm_provider import get_llm_provider_name
from app.engine.qdrant_client import delete_document as qdrant_delete_document
from app.models import Chat, Document, DocumentStatus, User

router = APIRouter(prefix="/api/chats/{chat_id}/documents", tags=["documents"])

# Where uploaded originals live on disk: storage/{user_id}/{document_id}/original.<ext>
# "storage" is gitignored (see .gitignore) and, under docker-compose, lives
# inside the bind-mounted ./backend directory so it survives container
# restarts without a dedicated volume.
STORAGE_ROOT = Path(os.getenv("STORAGE_DIR", "storage"))

# The on-disk extension is chosen from this fixed allow-list, never taken
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


# --- Endpoints ---------------------------------------------------------------


@router.post("", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def upload_document(
    chat_id: int,
    file: UploadFile,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Document:
    _get_owned_chat(db, chat_id, current_user)

    if not azure_ai_configured():
        raise _azure_not_configured_error()

    filename = file.filename or "upload"
    raw_bytes = await file.read()

    document = Document(
        user_id=current_user.id,
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
    doc_dir = STORAGE_ROOT / str(current_user.id) / str(document.id)
    doc_dir.mkdir(parents=True, exist_ok=True)
    storage_path = doc_dir / f"original.{ext}"
    storage_path.write_bytes(raw_bytes)

    document.storage_path = str(storage_path)
    db.commit()

    result = ingest_document(
        raw_bytes=raw_bytes,
        filename=filename,
        document_id=document.id,
        user_id=current_user.id,
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


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    chat_id: int,
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    _get_owned_chat(db, chat_id, current_user)
    document = _get_owned_document(db, chat_id, document_id, current_user)

    try:
        qdrant_delete_document(document.id)
    except Exception:  # noqa: BLE001 - a Qdrant hiccup shouldn't block deleting the DB row
        pass

    if document.storage_path:
        shutil.rmtree(Path(document.storage_path).parent, ignore_errors=True)

    db.delete(document)
    db.commit()
