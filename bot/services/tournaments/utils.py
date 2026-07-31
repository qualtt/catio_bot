from __future__ import annotations

import logging
from datetime import datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import config
from bot.content import bot_content
from db.crud import combine_slot, ensure_app_timezone, now_in_app_tz
from db.models.photo_tournament import (
    TOURNAMENT_CANCELLED,
    TOURNAMENT_COMPLETED,
    TOURNAMENT_MONTHLY,
    PhotoTournament,
    PhotoTournamentEntry,
)

logger = logging.getLogger(__name__)


def tournament_type_label(tournament_type: str) -> str:
    if tournament_type == TOURNAMENT_MONTHLY:
        return bot_content.message("tournament_monthly_label")
    return bot_content.message("tournament_weekly_label")


def tournament_period_label(tournament: PhotoTournament) -> str:
    start = ensure_app_timezone(tournament.period_start).strftime("%Y-%m-%d")
    end = ensure_app_timezone(tournament.period_end - timedelta(seconds=1)).strftime("%Y-%m-%d")
    return f"{start} - {end}"


def tournament_voting_deadline_label(tournament: PhotoTournament) -> str:
    if tournament.voting_ends_at is None:
        return "?"
    return ensure_app_timezone(tournament.voting_ends_at).strftime("%d.%m.%Y %H:%M")


def tournament_status_label(status: str) -> str:
    if status == TOURNAMENT_COMPLETED:
        return bot_content.message("tournament_status_completed")
    if status == TOURNAMENT_CANCELLED:
        return bot_content.message("tournament_status_cancelled")
    return bot_content.message("tournament_status_running")


async def tournament_status_text(session: AsyncSession, tournament: PhotoTournament) -> str:
    entry_count = (
        await session.scalar(
            select(func.count(PhotoTournamentEntry.id)).where(PhotoTournamentEntry.tournament_id == tournament.id)
        )
        or 0
    )
    return bot_content.message(
        "tournament_status",
        tournament_type=tournament_type_label(tournament.type),
        period=tournament_period_label(tournament),
        status=tournament_status_label(tournament.status),
        round_number=tournament.current_round_number,
        entry_count=entry_count,
        voting_deadline=tournament_voting_deadline_label(tournament),
    )


async def tournament_results_text(session: AsyncSession, tournament: PhotoTournament) -> str:
    status_text = await tournament_status_text(session, tournament)
    return bot_content.message(
        "tournament_results",
        status=status_text,
        winner_photo_id=tournament.winner_photo_id or "?",
        favorite_photo_id=tournament.favorite_photo_id or "?",
    )


def last_completed_week_period(
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    current = ensure_app_timezone(now or now_in_app_tz())
    current_monday = combine_slot(current.date() - timedelta(days=current.weekday()), time.min)
    return current_monday - timedelta(days=7), current_monday


def weekly_notification_time(period_end: datetime) -> datetime:
    notify_time = time(
        hour=max(0, min(config.PHOTO_TOURNAMENT_NOTIFY_HOUR, 23)),
        minute=max(0, min(config.PHOTO_TOURNAMENT_NOTIFY_MINUTE, 59)),
    )
    return combine_slot(ensure_app_timezone(period_end).date(), notify_time)


def round_duration() -> timedelta:
    return timedelta(hours=max(config.PHOTO_TOURNAMENT_ROUND_HOURS, 1))


def _bracket_round_count(entry_count: int) -> int:
    rounds = 0
    remaining = entry_count
    while remaining > 1:
        remaining = (remaining + 1) // 2
        rounds += 1
    return rounds
