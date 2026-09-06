"""
Message-sending endpoint: persist a user message, then stream back an
assistant reply from the agentic chat pipeline (app/engine/rag.py's
stream_agentic_reply).

This is one of the two places (with app/api/documents.py) that touches
both the DB/auth stack and app/engine/* - it checks auth/ownership,
persists the user Message row, calls the plain engine function (no ORM
objects in or out), streams the tokens back over Server-Sent Events as
they arrive, then persists the full assistant reply once the stream
completes.

Whether/what to search is entirely the model's decision (see
stream_agentic_reply's docstring and AGENT_SYSTEM_PROMPT in rag.py) - this
endpoint no longer pre-checks "does the user have any documents" or
branches between different canned/prompt paths in Python. The one thing
that stays a server-side fact, never something the model supplies: WHICH
user's (and optionally which chat's) documents the search tool is allowed
to touch.

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
from app.engine.rag import stream_agentic_reply
from app.models import Chat, Message, MessageRole, User

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

    # scope="all" (default): the search tool the model may call is allowed
    # to reach every chat this user owns (chat_id=None passed through to
    # retrieve()). scope="chat": restrict it to this chat only. Either way,
    # app/engine/qdrant_client.py's search() always filters by user_id -
    # that part is never optional, and it's the caller (this endpoint),
    # never the model, that supplies these values - see
    # stream_agentic_reply()'s docstring.
    scope_chat_id = chat_id if body.scope == "chat" else None
    # Captured as a plain int BEFORE event_stream() runs, not accessed as
    # current_user.id from inside it: event_stream() is a lazy generator
    # that only actually executes once the StreamingResponse starts
    # streaming, by which point FastAPI has already closed the
    # request-scoped `db` session and detached `current_user` - touching
    # any of its attributes at that point raises a SQLAlchemy
    # DetachedInstanceError.
    user_id = current_user.id

    async def event_stream() -> AsyncGenerator[str, None]:
        full_text = ""
        try:
            async for agent_event in stream_agentic_reply(
                body.content, user_id=user_id, chat_id=scope_chat_id, history=history
            ):
                # "status" events are real-time progress updates (see
                # AgentEvent's docstring) - forwarded to the client as
                # their own SSE event, never persisted and never counted
                # as part of the answer. Only "token" events are the
                # actual answer.
                if agent_event.type == "status":
                    yield _sse("status", {"message": agent_event.text})
                else:
                    full_text += agent_event.text
                    yield _sse("token", {"content": agent_event.text})
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
