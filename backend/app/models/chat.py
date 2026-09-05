"""Chat model - a conversation thread owned by a user.

Message/AI-response logic is out of scope for this phase; this is just the
data model plus CRUD (see app/api/chats.py). Ownership (`user_id`) is what
the later RAG phase relies on for per-user isolation, so every chat lookup
in the API layer must filter by the current user.
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
