from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class Photo(Base):
    __tablename__ = "photos"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_file_id: Mapped[str | None] = mapped_column(String, nullable=True)
    telegram_file_unique_id: Mapped[str | None] = mapped_column(String, unique=True, index=True, nullable=True)
    storage_bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1024), unique=True, nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    file_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, nullable=True)
    perceptual_hash: Mapped[str | None] = mapped_column(String(16), index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    posts: Mapped[list["Post"]] = relationship(back_populates="photo", foreign_keys="Post.photo_id")
    channel_history_items: Mapped[list["ChannelHistory"]] = relationship(back_populates="photo")
