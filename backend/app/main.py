"""
querynest backend entrypoint.

Phase 2 scope: adds authentication (email/password + Google OAuth) and
chat/message history (Postgres) on top of the Phase 1 scaffold (app
scaffold, health check, config-status endpoint). Document ingestion and
the actual RAG chat/retrieval logic are implemented in a later phase (see
README.md for the roadmap).
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.api.auth import router as auth_router
from app.api.chats import router as chats_router
from app.api.config_status import router as config_status_router
from app.core.config import get_settings

settings = get_settings()

app = FastAPI(
    title="querynest API",
    description="RAG-powered document chat assistant - backend API",
    version="0.2.0",
)

# Permissive CORS for local development. Tighten this once the frontend
# origin(s) are finalized for a real deployment.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Required by authlib's Google OAuth client (app/api/auth.py) to stash the
# CSRF `state` between /google/login and /google/callback. The secret only
# needs to be stable for the lifetime of that short redirect round-trip,
# so falling back to a fixed dev value when JWT_SECRET_KEY isn't set yet
# is fine - Google sign-in itself is already gated off (503) in that case.
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.JWT_SECRET_KEY or "dev-insecure-session-secret-change-me",
)

app.include_router(config_status_router)
app.include_router(auth_router)
app.include_router(chats_router)


@app.get("/health")
def health() -> dict[str, str]:
    """Basic liveness check used by docker-compose / uptime probes."""
    return {"status": "ok", "app": settings.APP_NAME}
