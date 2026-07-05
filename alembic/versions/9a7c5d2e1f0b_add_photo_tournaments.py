"""Add photo tournaments.

Revision ID: 9a7c5d2e1f0b
Revises: e8b1c4a9d2f3
Create Date: 2026-07-06 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9a7c5d2e1f0b"
down_revision: Union[str, None] = "e8b1c4a9d2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "photo_tournaments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("current_round_number", sa.Integer(), nullable=False),
        sa.Column("winner_photo_id", sa.Integer(), nullable=True),
        sa.Column("notification_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["winner_photo_id"], ["photos.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("type", "period_start", "period_end", name="uq_photo_tournaments_type_period"),
    )
    op.create_index("ix_photo_tournaments_period_start", "photo_tournaments", ["period_start"], unique=False)
    op.create_index("ix_photo_tournaments_period_end", "photo_tournaments", ["period_end"], unique=False)
    op.create_index("ix_photo_tournaments_status", "photo_tournaments", ["status"], unique=False)
    op.create_index("ix_photo_tournaments_type_status", "photo_tournaments", ["type", "status"], unique=False)

    op.create_table(
        "photo_tournament_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tournament_id", sa.Integer(), nullable=False),
        sa.Column("photo_id", sa.Integer(), nullable=False),
        sa.Column("source_post_id", sa.Integer(), nullable=True),
        sa.Column("source_channel_history_id", sa.Integer(), nullable=True),
        sa.Column("source_weekly_tournament_id", sa.Integer(), nullable=True),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["photo_id"], ["photos.id"]),
        sa.ForeignKeyConstraint(["source_channel_history_id"], ["channel_history.id"]),
        sa.ForeignKeyConstraint(["source_post_id"], ["posts.id"]),
        sa.ForeignKeyConstraint(["source_weekly_tournament_id"], ["photo_tournaments.id"]),
        sa.ForeignKeyConstraint(["tournament_id"], ["photo_tournaments.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tournament_id", "photo_id", name="uq_photo_tournament_entries_tournament_photo"),
        sa.UniqueConstraint("tournament_id", "seed", name="uq_photo_tournament_entries_tournament_seed"),
    )
    op.create_index(
        "ix_photo_tournament_entries_source_weekly",
        "photo_tournament_entries",
        ["source_weekly_tournament_id"],
        unique=False,
    )
    op.create_index(
        "ix_photo_tournament_entries_tournament_status",
        "photo_tournament_entries",
        ["tournament_id", "status"],
        unique=False,
    )

    op.create_table(
        "photo_tournament_rounds",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tournament_id", sa.Integer(), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tournament_id"], ["photo_tournaments.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tournament_id", "round_number", name="uq_photo_tournament_rounds_tournament_number"),
    )
    op.create_index(
        "ix_photo_tournament_rounds_status_ends_at",
        "photo_tournament_rounds",
        ["status", "ends_at"],
        unique=False,
    )

    op.create_table(
        "photo_tournament_matches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tournament_id", sa.Integer(), nullable=False),
        sa.Column("round_id", sa.Integer(), nullable=False),
        sa.Column("match_number", sa.Integer(), nullable=False),
        sa.Column("left_entry_id", sa.Integer(), nullable=False),
        sa.Column("right_entry_id", sa.Integer(), nullable=True),
        sa.Column("winner_entry_id", sa.Integer(), nullable=True),
        sa.Column("left_votes", sa.Integer(), nullable=False),
        sa.Column("right_votes", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["left_entry_id"], ["photo_tournament_entries.id"]),
        sa.ForeignKeyConstraint(["right_entry_id"], ["photo_tournament_entries.id"]),
        sa.ForeignKeyConstraint(["round_id"], ["photo_tournament_rounds.id"]),
        sa.ForeignKeyConstraint(["tournament_id"], ["photo_tournaments.id"]),
        sa.ForeignKeyConstraint(["winner_entry_id"], ["photo_tournament_entries.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("round_id", "match_number", name="uq_photo_tournament_matches_round_number"),
    )
    op.create_index(
        "ix_photo_tournament_matches_round_status",
        "photo_tournament_matches",
        ["round_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_photo_tournament_matches_tournament_status",
        "photo_tournament_matches",
        ["tournament_id", "status"],
        unique=False,
    )

    op.create_table(
        "photo_tournament_notifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tournament_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tournament_id"], ["photo_tournaments.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tournament_id", "user_id", name="uq_photo_tournament_notifications_tournament_user"),
    )
    op.create_index(
        "ix_photo_tournament_notifications_status",
        "photo_tournament_notifications",
        ["status"],
        unique=False,
    )

    op.create_table(
        "photo_tournament_votes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tournament_id", sa.Integer(), nullable=False),
        sa.Column("match_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("chosen_entry_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["chosen_entry_id"], ["photo_tournament_entries.id"]),
        sa.ForeignKeyConstraint(["match_id"], ["photo_tournament_matches.id"]),
        sa.ForeignKeyConstraint(["tournament_id"], ["photo_tournaments.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("match_id", "user_id", name="uq_photo_tournament_votes_match_user"),
    )
    op.create_index(
        "ix_photo_tournament_votes_tournament_user",
        "photo_tournament_votes",
        ["tournament_id", "user_id"],
        unique=False,
    )
    op.create_index(
        "ix_photo_tournament_votes_user_created_at",
        "photo_tournament_votes",
        ["user_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_photo_tournament_votes_user_created_at", table_name="photo_tournament_votes")
    op.drop_index("ix_photo_tournament_votes_tournament_user", table_name="photo_tournament_votes")
    op.drop_table("photo_tournament_votes")

    op.drop_index("ix_photo_tournament_notifications_status", table_name="photo_tournament_notifications")
    op.drop_table("photo_tournament_notifications")

    op.drop_index("ix_photo_tournament_matches_tournament_status", table_name="photo_tournament_matches")
    op.drop_index("ix_photo_tournament_matches_round_status", table_name="photo_tournament_matches")
    op.drop_table("photo_tournament_matches")

    op.drop_index("ix_photo_tournament_rounds_status_ends_at", table_name="photo_tournament_rounds")
    op.drop_table("photo_tournament_rounds")

    op.drop_index("ix_photo_tournament_entries_tournament_status", table_name="photo_tournament_entries")
    op.drop_index("ix_photo_tournament_entries_source_weekly", table_name="photo_tournament_entries")
    op.drop_table("photo_tournament_entries")

    op.drop_index("ix_photo_tournaments_type_status", table_name="photo_tournaments")
    op.drop_index("ix_photo_tournaments_status", table_name="photo_tournaments")
    op.drop_index("ix_photo_tournaments_period_end", table_name="photo_tournaments")
    op.drop_index("ix_photo_tournaments_period_start", table_name="photo_tournaments")
    op.drop_table("photo_tournaments")
