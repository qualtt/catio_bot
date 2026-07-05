"""Add submission album metadata to posts.

Revision ID: d4e8b7c2a9f1
Revises: c5a1d8f2e9b7
Create Date: 2026-07-05 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4e8b7c2a9f1"
down_revision: Union[str, None] = "c5a1d8f2e9b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("posts", sa.Column("submission_group_id", sa.String(length=120), nullable=True))
    op.add_column("posts", sa.Column("submission_group_index", sa.Integer(), nullable=True))
    op.add_column("posts", sa.Column("submission_group_size", sa.Integer(), nullable=True))
    op.create_index("ix_posts_submission_group_id", "posts", ["submission_group_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_posts_submission_group_id", table_name="posts")
    op.drop_column("posts", "submission_group_size")
    op.drop_column("posts", "submission_group_index")
    op.drop_column("posts", "submission_group_id")
