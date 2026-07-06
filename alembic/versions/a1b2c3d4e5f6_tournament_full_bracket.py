"""Tournament full bracket voting.

Revision ID: a1b2c3d4e5f6
Revises: 9a7c5d2e1f0b
Create Date: 2026-07-06 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "2f6d8a1b4c9e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("photo_tournaments", sa.Column("favorite_photo_id", sa.Integer(), nullable=True))
    op.add_column("photo_tournaments", sa.Column("voting_ends_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_photo_tournaments_favorite_photo_id",
        "photo_tournaments",
        "photos",
        ["favorite_photo_id"],
        ["id"],
    )
    op.add_column("photo_tournament_matches", sa.Column("feeder_left_match_id", sa.Integer(), nullable=True))
    op.add_column("photo_tournament_matches", sa.Column("feeder_right_match_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_photo_tournament_matches_feeder_left_match_id",
        "photo_tournament_matches",
        "photo_tournament_matches",
        ["feeder_left_match_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_photo_tournament_matches_feeder_right_match_id",
        "photo_tournament_matches",
        "photo_tournament_matches",
        ["feeder_right_match_id"],
        ["id"],
    )
    op.alter_column("photo_tournament_matches", "left_entry_id", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    op.alter_column("photo_tournament_matches", "left_entry_id", existing_type=sa.Integer(), nullable=False)
    op.drop_constraint("fk_photo_tournament_matches_feeder_right_match_id", "photo_tournament_matches", type_="foreignkey")
    op.drop_constraint("fk_photo_tournament_matches_feeder_left_match_id", "photo_tournament_matches", type_="foreignkey")
    op.drop_column("photo_tournament_matches", "feeder_right_match_id")
    op.drop_column("photo_tournament_matches", "feeder_left_match_id")
    op.drop_constraint("fk_photo_tournaments_favorite_photo_id", "photo_tournaments", type_="foreignkey")
    op.drop_column("photo_tournaments", "voting_ends_at")
    op.drop_column("photo_tournaments", "favorite_photo_id")
