"""Add scoring and old photo identification flow.

Revision ID: c5a1d8f2e9b7
Revises: b6f2a9d4e3c1
Create Date: 2026-07-05 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c5a1d8f2e9b7"
down_revision: Union[str, None] = "b6f2a9d4e3c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("channel_history", sa.Column("suggested_animal_type", sa.String(length=50), nullable=True))
    op.add_column("channel_history", sa.Column("review_status", sa.String(length=20), nullable=True))
    op.add_column("channel_history", sa.Column("review_sent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("channel_history", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_channel_history_review_status", "channel_history", ["review_status"], unique=False)

    op.create_table(
        "score_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "event_type",
            "entity_type",
            "entity_id",
            "user_id",
            name="uq_score_events_dedup",
        ),
    )
    op.create_index("ix_score_events_event_type", "score_events", ["event_type"], unique=False)
    op.create_index("ix_score_events_user_created_at", "score_events", ["user_id", "created_at"], unique=False)

    op.create_table(
        "photo_identification_assignments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("channel_history_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["channel_history_id"], ["channel_history.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "channel_history_id",
            "user_id",
            name="uq_photo_identification_assignments_item_user",
        ),
    )
    op.create_index(
        "ix_photo_identification_assignments_item_status",
        "photo_identification_assignments",
        ["channel_history_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_photo_identification_assignments_user_status",
        "photo_identification_assignments",
        ["user_id", "status"],
        unique=False,
    )

    op.create_table(
        "photo_identification_votes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("channel_history_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("animal_type", sa.String(length=50), nullable=False),
        sa.Column("is_correct", sa.Boolean(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["channel_history_id"], ["channel_history.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "channel_history_id",
            "user_id",
            name="uq_photo_identification_votes_item_user",
        ),
    )
    op.create_index(
        "ix_photo_identification_votes_item_reviewed",
        "photo_identification_votes",
        ["channel_history_id", "reviewed_at"],
        unique=False,
    )
    op.create_index(
        "ix_photo_identification_votes_user_created_at",
        "photo_identification_votes",
        ["user_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "photo_identification_batches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("animal_type", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("control_message_id", sa.BigInteger(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_photo_identification_batches_status",
        "photo_identification_batches",
        ["status"],
        unique=False,
    )

    op.create_table(
        "photo_identification_batch_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("channel_history_id", sa.Integer(), nullable=False),
        sa.Column("item_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["photo_identification_batches.id"]),
        sa.ForeignKeyConstraint(["channel_history_id"], ["channel_history.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "batch_id",
            "item_number",
            name="uq_photo_identification_batch_items_batch_number",
        ),
        sa.UniqueConstraint(
            "batch_id",
            "channel_history_id",
            name="uq_photo_identification_batch_items_batch_item",
        ),
    )


def downgrade() -> None:
    op.drop_table("photo_identification_batch_items")

    op.drop_index("ix_photo_identification_batches_status", table_name="photo_identification_batches")
    op.drop_table("photo_identification_batches")

    op.drop_index("ix_photo_identification_votes_user_created_at", table_name="photo_identification_votes")
    op.drop_index("ix_photo_identification_votes_item_reviewed", table_name="photo_identification_votes")
    op.drop_table("photo_identification_votes")

    op.drop_index(
        "ix_photo_identification_assignments_user_status",
        table_name="photo_identification_assignments",
    )
    op.drop_index(
        "ix_photo_identification_assignments_item_status",
        table_name="photo_identification_assignments",
    )
    op.drop_table("photo_identification_assignments")

    op.drop_index("ix_score_events_user_created_at", table_name="score_events")
    op.drop_index("ix_score_events_event_type", table_name="score_events")
    op.drop_table("score_events")

    op.drop_index("ix_channel_history_review_status", table_name="channel_history")
    op.drop_column("channel_history", "reviewed_at")
    op.drop_column("channel_history", "review_sent_at")
    op.drop_column("channel_history", "review_status")
    op.drop_column("channel_history", "suggested_animal_type")
