"""
Chat CRUD endpoints - create/list/get/delete chats and read their message
history. All endpoints require a valid access token (get_current_user) and
every lookup filters by the current user's id, which is what keeps one
user's chats invisible to another (the isolation the later RAG/document
phase builds on).

Sending a message and generating an assistant reply is NOT implemented
here - that's the document pipeline + RAG streaming phase. This is just
the data model plumbing: create a chat, list chats, fetch one chat with
its messages, delete a chat.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models import Chat, MessageRole, User

router = APIRouter(prefix="/api/chats", tags=["chats"])


# --- Schemas -----------------------------------------------------------------


class ChatCreate(BaseModel):
    title: str | None = None


class ChatOut(BaseModel):
    id: int
    title: str
    created_at: datetime

    model_config = {"from_attributes": True}


class MessageOut(BaseModel):
    id: int
    role: MessageRole
    content: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ChatDetailOut(ChatOut):
    messages: list[MessageOut]


# --- Helpers -------------------------------------------------------------


def _get_owned_chat(db: Session, chat_id: int, user: User) -> Chat:
    chat = db.get(Chat, chat_id)
    if chat is None or chat.user_id != user.id:
        # Deliberately identical to the "doesn't exist" case - a chat
        # belonging to another user must not be distinguishable from one
        # that was never created.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found")
    return chat


# --- Endpoints ---------------------------------------------------------------


@router.post("", response_model=ChatOut, status_code=status.HTTP_201_CREATED)
def create_chat(
    body: ChatCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Chat:
    chat = Chat(user_id=current_user.id, title=body.title or "New chat")
    db.add(chat)
    db.commit()
    db.refresh(chat)
    return chat


@router.get("", response_model=list[ChatOut])
def list_chats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[Chat]:
    return (
        db.query(Chat)
        .filter(Chat.user_id == current_user.id)
        .order_by(Chat.created_at.desc())
        .all()
    )


@router.get("/{chat_id}", response_model=ChatDetailOut)
def get_chat(
    chat_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Chat:
    chat = _get_owned_chat(db, chat_id, current_user)
    # chat.messages is already ordered by created_at (see Chat.messages
    # relationship's order_by), so no extra query is needed here.
    return chat


@router.delete("/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_chat(
    chat_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    chat = _get_owned_chat(db, chat_id, current_user)
    db.delete(chat)  # cascades to Message rows via the relationship + FK ondelete
    db.commit()
