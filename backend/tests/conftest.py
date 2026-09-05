"""
Shared pytest fixtures for the backend test suite.

Test database strategy
-----------------------
The integration tests (test_auth_api.py, test_chats_api.py,
test_documents_api.py) use FastAPI's TestClient against an in-memory
SQLite database, not the docker-compose Postgres instance. This is a
deliberate choice for this project:

  - The SQLAlchemy models here (backend/app/models/) are simple - plain
    columns, standard `Enum`/`ForeignKey` types, and ORM-level relationship
    cascades (`cascade="all, delete-orphan"`) rather than anything
    Postgres-specific (no JSONB, no array columns, no server-side
    triggers/functions). SQLite supports everything the API layer
    actually exercises in these tests.
  - `app/api/*.py` never writes raw SQL - every query goes through
    SQLAlchemy's Session/Query API, which SQLite and Postgres both satisfy
    the same way here.
  - SQLite-in-memory needs no running service, no migrations, and no
    teardown between test runs - each test gets a fresh schema
    (`Base.metadata.create_all` per test), which makes ownership/isolation
    assertions ("this row must not be visible to that user") trivial to
    reason about since there's no state left over from a previous test.

The tradeoff: this does not exercise Postgres-specific behavior (its
`Enum` type creates a real DB-level enum type in Postgres, for instance,
which SQLite does not). That's acceptable here because none of the tests
in this suite depend on that distinction - they exercise API/ORM-level
logic (auth, ownership, status transitions), not database-engine-specific
behavior. The Alembic migrations themselves are what's actually run
against real Postgres (see the README's "Database migrations" section);
these tests are not a substitute for running `alembic upgrade head`
against Postgres, they're a substitute for the manual curl-based checks
that used to be the only way to verify this code.

`engine/qdrant_client.py`'s isolation tests (test_qdrant_isolation.py) are
different: they run against a REAL Qdrant instance (the same one
docker-compose starts), using a disposable, uniquely-named collection per
test so they never touch the `querynest_documents` collection real
documents live in. Qdrant's filtering behavior is the single most
important property in this project, so it is tested against the real
thing rather than mocked.
"""

import os
import sys
import uuid
from pathlib import Path

# Make sure required env vars exist BEFORE any `app.*` module is imported -
# app/db/session.py reads Settings at import time to build its (unused in
# tests - see the `client` fixture below) module-level engine, and a
# missing JWT_SECRET_KEY would otherwise make every auth endpoint 503
# unless a test explicitly wants that (those tests unset it themselves via
# monkeypatch + get_settings.cache_clear()).
os.environ.setdefault("JWT_SECRET_KEY", "test-only-secret-not-for-real-use")
os.environ.setdefault("DATABASE_URL", "sqlite:///./_unused_test_default.db")

# backend/ (the parent of this tests/ dir) needs to be on sys.path so
# `import app...` resolves the same way it does for alembic/uvicorn -
# mirrors alembic/env.py's own sys.path handling.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401 - registers all models on Base.metadata
from app.core.config import get_settings
from app.db.base_class import Base
from app.db.session import get_db
from app.main import app

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture()
def db_engine():
    """A fresh in-memory SQLite database per test, with every table
    created from the same Base.metadata Alembic manages for Postgres."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture()
def client(db_engine, monkeypatch):
    """A TestClient wired to the per-test SQLite database via a
    dependency_overrides swap of get_db - the app's own module-level
    Postgres engine (app/db/session.py) is never touched."""
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    # A valid default so most tests don't have to think about JWT config -
    # tests that specifically exercise the "JWT not configured" 503 path
    # unset this themselves (see test_auth_api.py).
    monkeypatch.setenv("JWT_SECRET_KEY", "test-only-secret-not-for-real-use")
    get_settings.cache_clear()

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
    get_settings.cache_clear()


# Satisfies app/core/security.py's validate_password_strength() (min
# length, upper/lower/digit/special) - used as the default test password
# everywhere so a policy change only needs updating in one place.
VALID_TEST_PASSWORD = "A-long-Password-123!"


def unique_email(prefix: str = "user") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}@example.com"


def signup(client: TestClient, email: str | None = None, password: str = VALID_TEST_PASSWORD) -> dict:
    """Sign up a fresh user and return the token response body."""
    email = email or unique_email()
    response = client.post("/api/auth/signup", json={"email": email, "password": password})
    assert response.status_code == 201, response.text
    body = response.json()
    body["email"] = email
    body["password"] = password
    return body


def auth_headers(token_body: dict) -> dict:
    return {"Authorization": f"Bearer {token_body['access_token']}"}
