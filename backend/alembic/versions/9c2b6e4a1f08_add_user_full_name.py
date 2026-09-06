"""Add full_name column to users (avatar initials)

Revision ID: 9c2b6e4a1f08
Revises: 7f3c1a9d5e21
Create Date: 2026-09-06 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9c2b6e4a1f08'
down_revision: Union[str, None] = '7f3c1a9d5e21'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('full_name', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'full_name')
