"""
querynest backend entrypoint.

Phase 2 added authentication (email/password) and chat/message history
(Postgres). This phase adds document ingestion (app/api/documents.py) and
the actual RAG chat/retrieval logic (app/api/messages.py) - both
orchestrate between the DB/auth stack and the independent app/engine/
package (see app/engine/__init__.py for its isolation contract).
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth import router as auth_router
from app.api.chats import router as chats_router
from app.api.config_status import router as config_status_router
from app.api.documents import router as documents_router
from app.api.messages import router as messages_router
from app.core.config import get_settings

settings = get_settings()

app = FastAPI(
    title="querynest API",
    description="Private, secure document chat assistant - backend API",
    version="0.3.0",
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

app.include_router(config_status_router)
app.include_router(auth_router)
app.include_router(chats_router)
app.include_router(documents_router)
app.include_router(messages_router)


@app.get("/health")
def health() -> dict[str, str]:
    """Basic liveness check used by docker-compose / uptime probes."""
    return {"status": "ok", "app": settings.APP_NAME}
