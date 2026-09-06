"""Make documents.chat_id nullable (account-level document library)

Revision ID: 4a7e2c9f6b31
Revises: 9c2b6e4a1f08
Create Date: 2026-09-06 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4a7e2c9f6b31'
down_revision: Union[str, None] = '9c2b6e4a1f08'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('documents', 'chat_id', existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    op.alter_column('documents', 'chat_id', existing_type=sa.Integer(), nullable=False)
