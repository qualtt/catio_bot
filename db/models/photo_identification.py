from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PhotoIdentificationAssignment(Base):
    __tablename__ = "photo_identification_assignments"
    __table_args__ = (
        UniqueConstraint(
            "channel_history_id",
            "user_id",
            name="uq_photo_identification_assignments_item_user",
        ),
        Index("ix_photo_identification_assignments_user_status", "user_id", "status"),
        Index("ix_photo_identification_assignments_item_status", "channel_history_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_history_id: Mapped[int] = mapped_column(ForeignKey("channel_history.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="assigned")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    channel_history: Mapped["ChannelHistory"] = relationship(back_populates="identification_assignments")
    user: Mapped["User"] = relationship(back_populates="identification_assignments")


class PhotoIdentificationVote(Base):
    __tablename__ = "photo_identification_votes"
    __table_args__ = (
        UniqueConstraint(
            "channel_history_id",
            "user_id",
            name="uq_photo_identification_votes_item_user",
        ),
        Index("ix_photo_identification_votes_item_reviewed", "channel_history_id", "reviewed_at"),
        Index("ix_photo_identification_votes_user_created_at", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_history_id: Mapped[int] = mapped_column(ForeignKey("channel_history.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    animal_type: Mapped[str] = mapped_column(String(50), nullable=False)
    is_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    channel_history: Mapped["ChannelHistory"] = relationship(back_populates="identification_votes")
    user: Mapped["User"] = relationship(back_populates="identification_votes")


class PhotoIdentificationBatch(Base):
    __tablename__ = "photo_identification_batches"
    __table_args__ = (
        Index("ix_photo_identification_batches_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    animal_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    control_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    items: Mapped[list["PhotoIdentificationBatchItem"]] = relationship(
        back_populates="batch",
        order_by="PhotoIdentificationBatchItem.item_number",
    )


class PhotoIdentificationBatchItem(Base):
    __tablename__ = "photo_identification_batch_items"
    __table_args__ = (
        UniqueConstraint(
            "batch_id",
            "item_number",
            name="uq_photo_identification_batch_items_batch_number",
        ),
        UniqueConstraint(
            "batch_id",
            "channel_history_id",
            name="uq_photo_identification_batch_items_batch_item",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("photo_identification_batches.id"), nullable=False)
    channel_history_id: Mapped[int] = mapped_column(ForeignKey("channel_history.id"), nullable=False)
    item_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")

    batch: Mapped["PhotoIdentificationBatch"] = relationship(back_populates="items")
    channel_history: Mapped["ChannelHistory"] = relationship(back_populates="identification_batch_items")
