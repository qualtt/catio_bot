"""Add duplicate photo detection metadata.

Revision ID: b6f2a9d4e3c1
Revises: af3d91c2b7e4
Create Date: 2026-07-04 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b6f2a9d4e3c1"
down_revision: Union[str, None] = "af3d91c2b7e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("photos", sa.Column("perceptual_hash", sa.String(length=16), nullable=True))
    op.create_index("ix_photos_perceptual_hash", "photos", ["perceptual_hash"], unique=False)

    op.add_column("posts", sa.Column("duplicate_of_photo_id", sa.Integer(), nullable=True))
    op.add_column("posts", sa.Column("duplicate_distance", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_posts_duplicate_of_photo_id_photos",
        "posts",
        "photos",
        ["duplicate_of_photo_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_posts_duplicate_of_photo_id_photos", "posts", type_="foreignkey")
    op.drop_column("posts", "duplicate_distance")
    op.drop_column("posts", "duplicate_of_photo_id")

    op.drop_index("ix_photos_perceptual_hash", table_name="photos")
    op.drop_column("photos", "perceptual_hash")
