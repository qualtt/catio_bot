from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from db.models.photo import Photo
    from db.models.user import User
import enum

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class PostStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    PUBLISHED = "published"

class Post(Base):
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    photo_id: Mapped[int | None] = mapped_column(ForeignKey("photos.id"), nullable=True)
    duplicate_of_photo_id: Mapped[int | None] = mapped_column(ForeignKey("photos.id"), nullable=True)
    duplicate_distance: Mapped[int | None] = mapped_column(nullable=True)
    submission_group_id: Mapped[str | None] = mapped_column(String(120), index=True, nullable=True)
    submission_group_index: Mapped[int | None] = mapped_column(nullable=True)
    submission_group_size: Mapped[int | None] = mapped_column(nullable=True)
    file_id: Mapped[str] = mapped_column(String, index=True)
    animal_type: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[PostStatus] = mapped_column(default=PostStatus.PENDING)
    schedule_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_auto_scheduled: Mapped[bool] = mapped_column(Boolean, default=False)
    message_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="posts")
    photo: Mapped["Photo | None"] = relationship(back_populates="posts", foreign_keys=[photo_id])
    duplicate_of_photo: Mapped["Photo | None"] = relationship(foreign_keys=[duplicate_of_photo_id])
