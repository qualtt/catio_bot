from datetime import datetime
from sqlalchemy import BigInteger, String, ForeignKey, DateTime, Boolean, Enum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base
import enum

class PostStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    PUBLISHED = "published"

class Post(Base):
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    file_id: Mapped[str] = mapped_column(String, index=True)
    animal_type: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[PostStatus] = mapped_column(default=PostStatus.PENDING)
    schedule_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_auto_scheduled: Mapped[bool] = mapped_column(Boolean, default=False)
    message_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    # user = relationship("User", backref="posts")
