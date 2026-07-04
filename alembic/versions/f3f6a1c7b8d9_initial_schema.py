"""Initial schema.

Revision ID: f3f6a1c7b8d9
Revises:
Create Date: 2026-07-04 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "f3f6a1c7b8d9"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


post_status_enum = postgresql.ENUM(
    "PENDING",
    "APPROVED",
    "REJECTED",
    "PUBLISHED",
    name="poststatus",
)


def upgrade() -> None:
    post_status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_telegram_id", "users", ["telegram_id"], unique=True)

    op.create_table(
        "posts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("file_id", sa.String(), nullable=False),
        sa.Column("animal_type", sa.String(length=50), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(
                "PENDING",
                "APPROVED",
                "REJECTED",
                "PUBLISHED",
                name="poststatus",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("schedule_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_auto_scheduled", sa.Boolean(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_posts_file_id", "posts", ["file_id"], unique=False)

    op.create_table(
        "channel_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("file_id", sa.String(), nullable=False),
        sa.Column("animal_type", sa.String(length=50), nullable=True),
        sa.Column("identified_by", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["identified_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_channel_history_message_id",
        "channel_history",
        ["message_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_channel_history_message_id", table_name="channel_history")
    op.drop_table("channel_history")

    op.drop_index("ix_posts_file_id", table_name="posts")
    op.drop_table("posts")

    op.drop_index("ix_users_telegram_id", table_name="users")
    op.drop_table("users")

    post_status_enum.drop(op.get_bind(), checkfirst=True)
