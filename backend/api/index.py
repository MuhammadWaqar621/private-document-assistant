"""
Vercel Python serverless function entrypoint.

Vercel's Python runtime auto-detects an ASGI/WSGI `app` object in this
file - it never runs `uvicorn` itself, Vercel's own runtime serves the
ASGI app directly. This just imports and re-exports the real FastAPI app
from app/main.py so nothing about the application code needs to know it's
running on Vercel.

vercel.json (backend/vercel.json) routes every request to this function.
Alembic migrations are NOT run here - a serverless function has no
business running DDL at import/cold-start time, and every invocation
would re-attempt it. Run `alembic upgrade head` once, out-of-band, against
the provisioned DATABASE_URL after provisioning Vercel Postgres - see
README.md's "Vercel deployment" section.
"""

from app.main import app  # noqa: F401
