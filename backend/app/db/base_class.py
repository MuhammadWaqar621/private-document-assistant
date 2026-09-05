"""
Declarative base for all SQLAlchemy models.

Kept in its own tiny module (separate from session.py and models/) so that
Alembic's env.py can import `Base` and get the full `Base.metadata` for
autogeneration without needing to import the engine/session machinery.
"""

from sqlalchemy.orm import declarative_base

Base = declarative_base()
