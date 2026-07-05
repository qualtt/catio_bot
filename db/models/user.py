from sqlalchemy import BigInteger, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(255))
    full_name: Mapped[str | None] = mapped_column(String(255))
    score: Mapped[int] = mapped_column(default=0)

    posts: Mapped[list["Post"]] = relationship(back_populates="user")
    score_events: Mapped[list["ScoreEvent"]] = relationship(back_populates="user")
    identification_assignments: Mapped[list["PhotoIdentificationAssignment"]] = relationship(back_populates="user")
    identification_votes: Mapped[list["PhotoIdentificationVote"]] = relationship(back_populates="user")
