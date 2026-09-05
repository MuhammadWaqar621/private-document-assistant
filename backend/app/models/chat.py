"""Chat model - a conversation thread owned by a user.

Ownership (`user_id`) is what the RAG phase relies on for per-user
isolation - every chat lookup in the API layer filters by the current
user. Documents uploaded to a chat (see app/models/document.py) are scoped
to that chat only: the Qdrant payload filter in
app/engine/qdrant_client.py enforces that a document uploaded here is
never retrievable from a different chat, even for the same user.
"""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.db.base_class import Base


class Chat(Base):
    __tablename__ = "chats"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String, nullable=False, default="New chat")
    created_at = Column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    user = relationship("User", back_populates="chats")
    messages = relationship(
        "Message",
        back_populates="chat",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )
    documents = relationship(
        "Document",
        back_populates="chat",
        cascade="all, delete-orphan",
        order_by="Document.created_at",
    )
