"""Add is_muted to users

Revision ID: d467e86b042d
Revises: c7d8e9f0a1b2
Create Date: 2026-07-27 19:28:46.649300
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'd467e86b042d'
down_revision: str | None = 'c7d8e9f0a1b2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('users', sa.Column('is_muted', sa.Boolean(), server_default='false', nullable=False))


def downgrade() -> None:
    op.drop_column('users', 'is_muted')
