"""Add S3 photo storage metadata.

Revision ID: af3d91c2b7e4
Revises: 7e2f4d9c8a1b
Create Date: 2026-07-04 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "af3d91c2b7e4"
down_revision: Union[str, None] = "7e2f4d9c8a1b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "photos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_file_id", sa.String(), nullable=True),
        sa.Column("telegram_file_unique_id", sa.String(), nullable=True),
        sa.Column("storage_bucket", sa.String(length=255), nullable=False),
        sa.Column("storage_key", sa.String(length=1024), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=True),
        sa.Column("file_size", sa.BigInteger(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key", name="uq_photos_storage_key"),
    )
    op.create_index("ix_photos_telegram_file_unique_id", "photos", ["telegram_file_unique_id"], unique=True)
    op.create_index("ix_photos_sha256", "photos", ["sha256"], unique=True)

    op.add_column("posts", sa.Column("photo_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_posts_photo_id_photos", "posts", "photos", ["photo_id"], ["id"])

    op.add_column("channel_history", sa.Column("photo_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_channel_history_photo_id_photos",
        "channel_history",
        "photos",
        ["photo_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_channel_history_photo_id_photos", "channel_history", type_="foreignkey")
    op.drop_column("channel_history", "photo_id")

    op.drop_constraint("fk_posts_photo_id_photos", "posts", type_="foreignkey")
    op.drop_column("posts", "photo_id")

    op.drop_index("ix_photos_sha256", table_name="photos")
    op.drop_index("ix_photos_telegram_file_unique_id", table_name="photos")
    op.drop_table("photos")
