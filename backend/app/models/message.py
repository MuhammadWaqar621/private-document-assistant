"""Message model - a single turn (user or assistant) within a Chat.

Sending a message and generating an assistant reply (the actual RAG logic)
is implemented in a later phase (document pipeline + retrieval). For now
this is just the persisted shape so chat history can be created/read.
"""

import enum
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Integer, Text
from sqlalchemy.orm import relationship

from app.db.base_class import Base


class MessageRole(str, enum.Enum):
    user = "user"
    assistant = "assistant"


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(Integer, ForeignKey("chats.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(Enum(MessageRole, name="message_role"), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    chat = relationship("Chat", back_populates="messages")
