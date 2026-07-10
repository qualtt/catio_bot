from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


TOURNAMENT_WEEKLY = "weekly"
TOURNAMENT_MONTHLY = "monthly"

TOURNAMENT_DRAFT = "draft"
TOURNAMENT_RUNNING = "running"
TOURNAMENT_COMPLETED = "completed"
TOURNAMENT_CANCELLED = "cancelled"

ENTRY_ACTIVE = "active"
ENTRY_ELIMINATED = "eliminated"
ENTRY_WINNER = "winner"

ROUND_OPEN = "open"
ROUND_CLOSED = "closed"

MATCH_OPEN = "open"
MATCH_CLOSED = "closed"
MATCH_BYE = "bye"

NOTIFICATION_SENT = "sent"
NOTIFICATION_FAILED = "failed"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PhotoTournament(Base):
    __tablename__ = "photo_tournaments"
    __table_args__ = (
        UniqueConstraint("type", "period_start", "period_end", name="uq_photo_tournaments_type_period"),
        Index("ix_photo_tournaments_type_status", "type", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=TOURNAMENT_DRAFT, index=True)
    current_round_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    winner_photo_id: Mapped[int | None] = mapped_column(ForeignKey("photos.id"), nullable=True)
    favorite_photo_id: Mapped[int | None] = mapped_column(ForeignKey("photos.id"), nullable=True)
    voting_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notification_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    results_notification_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    winner_photo = relationship("Photo", foreign_keys=[winner_photo_id])
    favorite_photo = relationship("Photo", foreign_keys=[favorite_photo_id])
    entries: Mapped[list["PhotoTournamentEntry"]] = relationship(
        back_populates="tournament",
        foreign_keys="PhotoTournamentEntry.tournament_id",
        order_by="PhotoTournamentEntry.seed",
    )
    rounds: Mapped[list["PhotoTournamentRound"]] = relationship(
        back_populates="tournament",
        order_by="PhotoTournamentRound.round_number",
    )
    notifications: Mapped[list["PhotoTournamentNotification"]] = relationship(back_populates="tournament")


class PhotoTournamentEntry(Base):
    __tablename__ = "photo_tournament_entries"
    __table_args__ = (
        UniqueConstraint("tournament_id", "photo_id", name="uq_photo_tournament_entries_tournament_photo"),
        UniqueConstraint("tournament_id", "seed", name="uq_photo_tournament_entries_tournament_seed"),
        Index("ix_photo_tournament_entries_tournament_status", "tournament_id", "status"),
        Index("ix_photo_tournament_entries_source_weekly", "source_weekly_tournament_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("photo_tournaments.id"), nullable=False)
    photo_id: Mapped[int] = mapped_column(ForeignKey("photos.id"), nullable=False)
    source_post_id: Mapped[int | None] = mapped_column(ForeignKey("posts.id"), nullable=True)
    source_channel_history_id: Mapped[int | None] = mapped_column(ForeignKey("channel_history.id"), nullable=True)
    source_weekly_tournament_id: Mapped[int | None] = mapped_column(
        ForeignKey("photo_tournaments.id"),
        nullable=True,
    )
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ENTRY_ACTIVE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    tournament: Mapped["PhotoTournament"] = relationship(
        back_populates="entries",
        foreign_keys=[tournament_id],
    )
    photo = relationship("Photo")
    source_post = relationship("Post")
    source_channel_history = relationship("ChannelHistory")
    source_weekly_tournament = relationship("PhotoTournament", foreign_keys=[source_weekly_tournament_id])


class PhotoTournamentRound(Base):
    __tablename__ = "photo_tournament_rounds"
    __table_args__ = (
        UniqueConstraint("tournament_id", "round_number", name="uq_photo_tournament_rounds_tournament_number"),
        Index("ix_photo_tournament_rounds_status_ends_at", "status", "ends_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("photo_tournaments.id"), nullable=False)
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ROUND_OPEN)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tournament: Mapped["PhotoTournament"] = relationship(back_populates="rounds")
    matches: Mapped[list["PhotoTournamentMatch"]] = relationship(
        back_populates="round",
        order_by="PhotoTournamentMatch.match_number",
    )


class PhotoTournamentMatch(Base):
    __tablename__ = "photo_tournament_matches"
    __table_args__ = (
        UniqueConstraint("round_id", "match_number", name="uq_photo_tournament_matches_round_number"),
        Index("ix_photo_tournament_matches_tournament_status", "tournament_id", "status"),
        Index("ix_photo_tournament_matches_round_status", "round_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("photo_tournaments.id"), nullable=False)
    round_id: Mapped[int] = mapped_column(ForeignKey("photo_tournament_rounds.id"), nullable=False)
    match_number: Mapped[int] = mapped_column(Integer, nullable=False)
    feeder_left_match_id: Mapped[int | None] = mapped_column(
        ForeignKey("photo_tournament_matches.id"),
        nullable=True,
    )
    feeder_right_match_id: Mapped[int | None] = mapped_column(
        ForeignKey("photo_tournament_matches.id"),
        nullable=True,
    )
    left_entry_id: Mapped[int | None] = mapped_column(ForeignKey("photo_tournament_entries.id"), nullable=True)
    right_entry_id: Mapped[int | None] = mapped_column(ForeignKey("photo_tournament_entries.id"), nullable=True)
    winner_entry_id: Mapped[int | None] = mapped_column(ForeignKey("photo_tournament_entries.id"), nullable=True)
    left_votes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    right_votes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=MATCH_OPEN)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tournament: Mapped["PhotoTournament"] = relationship()
    round: Mapped["PhotoTournamentRound"] = relationship(back_populates="matches")
    feeder_left_match: Mapped["PhotoTournamentMatch | None"] = relationship(
        foreign_keys=[feeder_left_match_id],
        remote_side="PhotoTournamentMatch.id",
    )
    feeder_right_match: Mapped["PhotoTournamentMatch | None"] = relationship(
        foreign_keys=[feeder_right_match_id],
        remote_side="PhotoTournamentMatch.id",
    )
    left_entry: Mapped["PhotoTournamentEntry"] = relationship(foreign_keys=[left_entry_id])
    right_entry: Mapped["PhotoTournamentEntry | None"] = relationship(foreign_keys=[right_entry_id])
    winner_entry: Mapped["PhotoTournamentEntry | None"] = relationship(foreign_keys=[winner_entry_id])
    votes: Mapped[list["PhotoTournamentVote"]] = relationship(back_populates="match")


class PhotoTournamentVote(Base):
    __tablename__ = "photo_tournament_votes"
    __table_args__ = (
        UniqueConstraint("match_id", "user_id", name="uq_photo_tournament_votes_match_user"),
        Index("ix_photo_tournament_votes_tournament_user", "tournament_id", "user_id"),
        Index("ix_photo_tournament_votes_user_created_at", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("photo_tournaments.id"), nullable=False)
    match_id: Mapped[int] = mapped_column(ForeignKey("photo_tournament_matches.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    chosen_entry_id: Mapped[int] = mapped_column(ForeignKey("photo_tournament_entries.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    tournament: Mapped["PhotoTournament"] = relationship()
    match: Mapped["PhotoTournamentMatch"] = relationship(back_populates="votes")
    user = relationship("User")
    chosen_entry: Mapped["PhotoTournamentEntry"] = relationship()


class PhotoTournamentNotification(Base):
    __tablename__ = "photo_tournament_notifications"
    __table_args__ = (
        UniqueConstraint("tournament_id", "user_id", name="uq_photo_tournament_notifications_tournament_user"),
        Index("ix_photo_tournament_notifications_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("photo_tournaments.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=NOTIFICATION_SENT)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    tournament: Mapped["PhotoTournament"] = relationship(back_populates="notifications")
    user = relationship("User")
