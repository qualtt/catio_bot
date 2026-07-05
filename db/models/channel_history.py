from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base

class ChannelHistory(Base):
    __tablename__ = "channel_history"
    __table_args__ = (
        UniqueConstraint("chat_id", "message_id", name="uq_channel_history_chat_message"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int | None] = mapped_column(BigInteger, index=True, nullable=True)
    message_id: Mapped[int] = mapped_column(BigInteger, index=True)
    photo_id: Mapped[int | None] = mapped_column(ForeignKey("photos.id"), nullable=True)
    file_id: Mapped[str] = mapped_column(String)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True, nullable=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_group_id: Mapped[str | None] = mapped_column(String(100), index=True, nullable=True)
    animal_type: Mapped[str | None] = mapped_column(String(50))
    identified_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    suggested_animal_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    review_status: Mapped[str | None] = mapped_column(String(20), index=True, nullable=True)
    review_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    photo: Mapped["Photo | None"] = relationship(back_populates="channel_history_items")
    identification_assignments: Mapped[list["PhotoIdentificationAssignment"]] = relationship(
        back_populates="channel_history"
    )
    identification_votes: Mapped[list["PhotoIdentificationVote"]] = relationship(back_populates="channel_history")
    identification_batch_items: Mapped[list["PhotoIdentificationBatchItem"]] = relationship(
        back_populates="channel_history"
    )
