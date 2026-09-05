"""User account model.

A user authenticates either with an email/password (`hashed_password` set,
`google_id` null) or via Google OAuth (`google_id` set, `hashed_password`
may be null if they never set a password). Both paths can eventually apply
to the same row if a Google user later sets a password, or a
password-based user later links Google - lookups in the auth endpoints
check both `email` and `google_id` to support that.
"""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.orm import relationship

from app.db.base_class import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, nullable=False, index=True)
    hashed_password = Column(String, nullable=True)
    google_id = Column(String, unique=True, nullable=True, index=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    chats = relationship("Chat", back_populates="user", cascade="all, delete-orphan")
    password_reset_tokens = relationship(
        "PasswordResetToken", back_populates="user", cascade="all, delete-orphan"
    )
