from sqlalchemy import BigInteger, String, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base

class ChannelHistory(Base):
    __tablename__ = "channel_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    file_id: Mapped[str] = mapped_column(String)
    animal_type: Mapped[str | None] = mapped_column(String(50))
    identified_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
