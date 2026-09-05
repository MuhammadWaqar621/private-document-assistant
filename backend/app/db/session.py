"""
SQLAlchemy engine/session setup.

Sync SQLAlchemy (psycopg2 driver) - simpler than async SQLAlchemy and
plenty fast enough for this project's request volume. `get_db` is a
FastAPI dependency that yields a session per-request and always closes it.
"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()

engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: yield a request-scoped SQLAlchemy session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
