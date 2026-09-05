"""
Message-sending endpoint: persist a user message, then stream back an
assistant reply grounded in the chat's own uploaded documents (RAG).

This is one of the two places (with app/api/documents.py) that touches
both the DB/auth stack and app/engine/* - it checks auth/ownership,
persists the user Message row, calls the plain engine functions
(engine.rag.retrieve / engine.rag.stream_answer - no ORM objects in or
out), streams the tokens back over Server-Sent Events as they arrive, then
persists the full assistant reply once the stream completes.
"""

import json
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import SessionLocal, get_db
from app.engine.azure_client import azure_ai_configured
from app.engine.rag import retrieve, stream_answer
from app.models import Chat, Message, MessageRole, User

router = APIRouter(prefix="/api/chats/{chat_id}/messages", tags=["messages"])


class MessageCreate(BaseModel):
    content: str


def _get_owned_chat(db: Session, chat_id: int, user: User) -> Chat:
    chat = db.get(Chat, chat_id)
    if chat is None or chat.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found")
    return chat


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.post("")
async def send_message(
    chat_id: int,
    body: MessageCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    chat = _get_owned_chat(db, chat_id, current_user)

    if not azure_ai_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "azure_ai_not_configured",
                "message": "Azure OpenAI is not configured. Set AZURE_EM_*/LLM_ENDPOINT_MINI_MODEL* in .env.",
            },
        )

    if not body.content.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Message content cannot be empty")

    # Prior turns, as plain dicts (never ORM objects) - app.engine.rag never
    # sees a SQLAlchemy Message, satisfying the engine's isolation contract.
    history = [{"role": m.role.value, "content": m.content} for m in chat.messages]

    user_message = Message(chat_id=chat_id, role=MessageRole.user, content=body.content)
    db.add(user_message)
    db.commit()

    # Retrieval is scoped to (user_id, chat_id) - see
    # app/engine/qdrant_client.py's search() filter, the isolation
    # boundary this whole feature depends on.
    retrieved = retrieve(body.content, user_id=current_user.id, chat_id=chat_id)

    async def event_stream() -> AsyncGenerator[str, None]:
        full_text = ""
        try:
            async for token in stream_answer(body.content, retrieved, history):
                full_text += token
                yield _sse("token", {"content": token})
        except Exception as exc:  # noqa: BLE001 - surface the failure over SSE, don't just hang up
            yield _sse("error", {"message": str(exc)})

        # A fresh session (rather than the request-scoped `db` above) is
        # used for this final write: FastAPI's yield-dependency cleanup for
        # `db` is tied to the request handler's scope, which is a subtler
        # lifetime to reason about once a StreamingResponse is involved -
        # opening a short-lived session here sidesteps that entirely.
        if full_text:
            write_db = SessionLocal()
            try:
                write_db.add(Message(chat_id=chat_id, role=MessageRole.assistant, content=full_text))
                write_db.commit()
            finally:
                write_db.close()

        yield _sse("done", {})

    return StreamingResponse(event_stream(), media_type="text/event-stream")
