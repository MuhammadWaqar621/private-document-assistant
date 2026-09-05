"""
app.engine - the AI/RAG engine, kept fully independent of the rest of the
FastAPI application.

Isolation contract (do not violate this):
  - Nothing in this package imports from app.api, app.models (SQLAlchemy),
    or any auth code (app.core.security / app.api.deps).
  - Every function here takes plain arguments (ints, strings, raw bytes,
    plain dicts/dataclasses) and returns plain Python objects - never an
    ORM object, never a FastAPI Request/Response.
  - Configuration is read directly from environment variables (see
    engine/azure_client.py, engine/qdrant_client.py) rather than through
    app.core.config.Settings, so this package has zero dependency on the
    rest of the app and can be imported/tested/reused in isolation (e.g.
    in a standalone script, a notebook, or a different service entirely).

The API layer (app/api/documents.py, app/api/messages.py) is the ONLY code
that is allowed to touch both the DB/auth stack and this package - it
checks auth/ownership, calls a plain engine function, and persists the
result.

Modules:
  - azure_client.py: Azure OpenAI client construction (embeddings + chat)
  - extraction.py:   raw file bytes -> [(page_number, text), ...]
  - chunking.py:     page text -> embedding-sized chunks
  - qdrant_client.py: vector storage/search, with per-(user_id, chat_id)
                      tenant isolation enforced in every query
  - ingestion.py:     extract -> chunk -> embed -> upsert, no DB writes
  - rag.py:           retrieve() + stream_answer() for the chat endpoint
"""
