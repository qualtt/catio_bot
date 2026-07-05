"""Add channel history metadata.

Revision ID: e8b1c4a9d2f3
Revises: d4e8b7c2a9f1
Create Date: 2026-07-05 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e8b1c4a9d2f3"
down_revision: Union[str, None] = "d4e8b7c2a9f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("channel_history", sa.Column("chat_id", sa.BigInteger(), nullable=True))
    op.add_column("channel_history", sa.Column("published_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("channel_history", sa.Column("caption", sa.Text(), nullable=True))
    op.add_column("channel_history", sa.Column("media_group_id", sa.String(length=100), nullable=True))

    op.drop_index("ix_channel_history_message_id", table_name="channel_history")
    op.create_index("ix_channel_history_message_id", "channel_history", ["message_id"], unique=False)
    op.create_index("ix_channel_history_chat_id", "channel_history", ["chat_id"], unique=False)
    op.create_index("ix_channel_history_published_at", "channel_history", ["published_at"], unique=False)
    op.create_index("ix_channel_history_media_group_id", "channel_history", ["media_group_id"], unique=False)
    op.create_unique_constraint(
        "uq_channel_history_chat_message",
        "channel_history",
        ["chat_id", "message_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_channel_history_chat_message", "channel_history", type_="unique")
    op.drop_index("ix_channel_history_media_group_id", table_name="channel_history")
    op.drop_index("ix_channel_history_published_at", table_name="channel_history")
    op.drop_index("ix_channel_history_chat_id", table_name="channel_history")
    op.drop_index("ix_channel_history_message_id", table_name="channel_history")
    op.create_index("ix_channel_history_message_id", "channel_history", ["message_id"], unique=True)

    op.drop_column("channel_history", "media_group_id")
    op.drop_column("channel_history", "caption")
    op.drop_column("channel_history", "published_at")
    op.drop_column("channel_history", "chat_id")
