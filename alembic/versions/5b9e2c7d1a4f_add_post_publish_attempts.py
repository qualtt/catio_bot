"""Add publish attempt counter to posts.

Revision ID: 5b9e2c7d1a4f
Revises: d467e86b042d
Create Date: 2026-09-29 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "5b9e2c7d1a4f"
down_revision: str | None = "d467e86b042d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("posts", sa.Column("publish_attempts", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("posts", "publish_attempts")
