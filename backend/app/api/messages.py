"""
Message-sending endpoint: persist a user message, then stream back an
assistant reply grounded in the user's uploaded documents (RAG).

This is one of the two places (with app/api/documents.py) that touches
both the DB/auth stack and app/engine/* - it checks auth/ownership,
persists the user Message row, calls the plain engine functions
(engine.rag.retrieve / engine.rag.stream_answer - no ORM objects in or
out), streams the tokens back over Server-Sent Events as they arrive, then
persists the full assistant reply once the stream completes.

Retrieval scope: `MessageCreate.scope` picks between the two supported
modes - "all" (the default) searches every document the current user has
uploaded across every one of their chats, "chat" restricts retrieval to
just this chat's uploads. Either way, retrieval is always scoped to the
current user (app/engine/qdrant_client.py's `user_id` filter is
unconditional) - only the `chat_id` narrowing is opt-in.
"""

import json
from typing import AsyncGenerator, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import SessionLocal, get_db
from app.engine.azure_client import azure_ai_configured
from app.engine.llm_provider import get_llm_provider_name
from app.engine.rag import retrieve, stream_answer, stream_onboarding_reply
from app.models import Chat, Document, DocumentStatus, Message, MessageRole, User

router = APIRouter(prefix="/api/chats/{chat_id}/messages", tags=["messages"])


class MessageCreate(BaseModel):
    content: str
    # "all" (default): retrieve from every document this user has uploaded,
    # across all of their chats. "chat": restrict retrieval to just this
    # chat's uploads (opt-in narrowing - see app/engine/qdrant_client.py's
    # search() docstring for the underlying Qdrant filter).
    scope: Literal["all", "chat"] = "all"


def _get_owned_chat(db: Session, chat_id: int, user: User) -> Chat:
    chat = db.get(Chat, chat_id)
    if chat is None or chat.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found")
    return chat


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


_TITLE_MAX_LENGTH = 50
_DEFAULT_TITLE = "New chat"

def _derive_title(content: str) -> str:
    """A short chat title from the first message's content, truncated at a
    word boundary rather than mid-word."""
    text = content.strip().splitlines()[0]
    if len(text) <= _TITLE_MAX_LENGTH:
        return text
    truncated = text[:_TITLE_MAX_LENGTH].rsplit(" ", 1)[0]
    return (truncated or text[:_TITLE_MAX_LENGTH]).rstrip() + "..."


@router.post("")
async def send_message(
    chat_id: int,
    body: MessageCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    chat = _get_owned_chat(db, chat_id, current_user)

    if not azure_ai_configured():
        # Error code kept as "azure_ai_not_configured" for backward
        # compatibility (see app/engine/azure_client.py's
        # azure_ai_configured() docstring); the message is provider-aware
        # since the chat half may now be Groq-backed (LLM_PROVIDER,
        # default "groq" - see app/engine/llm_provider.py).
        provider = get_llm_provider_name()
        chat_hint = "LLM_ENDPOINT*" if provider == "azure" else "GROQ_API_KEY"
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "azure_ai_not_configured",
                "message": (
                    "AI is not configured. Set AZURE_EM_* (embeddings) and "
                    f"{chat_hint} (chat, provider={provider}) in .env."
                ),
            },
        )

    if not body.content.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Message content cannot be empty")

    # Prior turns, as plain dicts (never ORM objects) - app.engine.rag never
    # sees a SQLAlchemy Message, satisfying the engine's isolation contract.
    history = [{"role": m.role.value, "content": m.content} for m in chat.messages]

    # Auto-title the chat from its first message, the same way ChatGPT/
    # Claude do - only when this is genuinely the first message AND the
    # chat still has the generic default title (never overwrite a title
    # the user set explicitly when creating the chat).
    if not history and chat.title == _DEFAULT_TITLE:
        chat.title = _derive_title(body.content)
        db.add(chat)

    user_message = Message(chat_id=chat_id, role=MessageRole.user, content=body.content)
    db.add(user_message)
    db.commit()

    # Hard gate (code-level, not just a prompt instruction an LLM could be
    # talked past): if the user has NO ready documents anywhere in the
    # requested scope, refuse before ever calling the LLM, rather than
    # letting this endpoint quietly become a generic no-grounding chatbot.
    # This is a real DB check (not app/engine/*, which has no DB access) -
    # scoped the same way retrieval itself is scoped below.
    documents_query = db.query(Document).filter(
        Document.user_id == current_user.id, Document.status == DocumentStatus.ready
    )
    if body.scope == "chat":
        documents_query = documents_query.filter(Document.chat_id == chat_id)
    has_any_ready_document = db.query(documents_query.exists()).scalar()

    # scope="all" (default): retrieve from every chat this user owns
    # (chat_id=None - no chat_id filter applied in Qdrant). scope="chat":
    # restrict to this chat only. Either way, app/engine/qdrant_client.py's
    # search() always filters by user_id - that part is never optional.
    retrieved = (
        retrieve(
            body.content,
            user_id=current_user.id,
            chat_id=chat_id if body.scope == "chat" else None,
        )
        if has_any_ready_document
        else []
    )

    async def event_stream() -> AsyncGenerator[str, None]:
        full_text = ""
        try:
            # No ready documents anywhere in scope: still a REAL LLM call
            # (not a hardcoded string), but through stream_onboarding_reply()
            # - a tightly-scoped system prompt (see app/engine/rag.py) that
            # can greet naturally and answer "what is QueryNest"/"who built
            # this" in its own words, while being explicitly instructed to
            # refuse - not answer from general knowledge - anything that
            # actually needs real information. The hard gate is still real:
            # has_any_ready_document is a DB fact checked above, not
            # something a clever prompt can talk the model out of; only the
            # *reply itself*, for this specific "nothing uploaded yet"
            # moment, is now LLM-generated instead of regex-matched.
            token_source = (
                stream_onboarding_reply(body.content, history)
                if not has_any_ready_document
                else stream_answer(body.content, retrieved, history)
            )
            async for token in token_source:
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
