"""Add pgvector extension + document_chunks table (replaces Qdrant)

Revision ID: 5e8a1c3f9b02
Revises: 4a7e2c9f6b31
Create Date: 2026-09-15 00:00:00.000000

Replaces the Qdrant vector store with pgvector on this SAME Postgres
database - see app/engine/vector_store.py, which reads/writes this table.
The embedding column is sized to AZURE_EM_DIMENSIONS (default 1536, the
same default app/engine/azure_client.get_embedding_dimensions() uses) -
override the `vector(...)` size below if your embedding deployment uses a
different dimensionality (e.g. 3072 for text-embedding-3-large) before
running this migration.

Run this once, out-of-band, against the provisioned database - Vercel's
Python serverless function never runs migrations at import time (see
README.md's "Vercel deployment" section):

    cd backend
    DATABASE_URL=<vercel-postgres-connection-string> alembic upgrade head
"""

from typing import Sequence, Union

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '5e8a1c3f9b02'
down_revision: Union[str, None] = '4a7e2c9f6b31'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Must match app/engine/azure_client.DEFAULT_EMBEDDING_DIMENSIONS /
# AZURE_EM_DIMENSIONS - see the module docstring above.
EMBEDDING_DIMENSIONS = 1536


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("chat_id", sa.Integer(), nullable=True),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIMENSIONS), nullable=False),
        sa.UniqueConstraint(
            "document_id", "chunk_index", name="uq_document_chunks_document_id_chunk_index"
        ),
    )
    op.create_index(
        "ix_document_chunks_document_id", "document_chunks", ["document_id"]
    )
    op.create_index("ix_document_chunks_user_id", "document_chunks", ["user_id"])
    op.create_index("ix_document_chunks_chat_id", "document_chunks", ["chat_id"])

    # Approximate-nearest-neighbor index for cosine distance (`<=>`), the
    # operator app/engine/vector_store.py's search() uses via pgvector's
    # Vector.cosine_distance(). HNSW needs pgvector >= 0.5.0 - Vercel
    # Postgres (Neon-backed) ships a recent enough version; if a much
    # older pgvector is ever targeted, swap this for an ivfflat index
    # instead (requires an approximate row-count `lists` parameter and
    # some existing data to train well, unlike hnsw).
    op.execute(
        "CREATE INDEX ix_document_chunks_embedding_cosine ON document_chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.drop_index("ix_document_chunks_embedding_cosine", table_name="document_chunks")
    op.drop_index("ix_document_chunks_chat_id", table_name="document_chunks")
    op.drop_index("ix_document_chunks_user_id", table_name="document_chunks")
    op.drop_index("ix_document_chunks_document_id", table_name="document_chunks")
    op.drop_table("document_chunks")
    # Deliberately not dropping the `vector` extension - other objects (or
    # a future migration) may still depend on it, and CREATE EXTENSION IF
    # NOT EXISTS in upgrade() is already a no-op if it's still present.
