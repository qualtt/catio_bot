from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.crud import now_in_app_tz
from db.models.channel_history import ChannelHistory
from db.models.photo_tournament import (
    MATCH_OPEN,
    TOURNAMENT_COMPLETED,
    TOURNAMENT_MONTHLY,
    TOURNAMENT_RUNNING,
    PhotoTournament,
    PhotoTournamentEntry,
    PhotoTournamentMatch,
    PhotoTournamentRound,
    PhotoTournamentVote,
)
from db.models.post import Post, PostStatus

from .models import TournamentMatchView, TournamentSourcePhoto
from .voting import (
    _entry_with_photo,
    _voting_is_open,
    resolve_user_match_view,
)

logger = logging.getLogger(__name__)


async def collect_weekly_source_photos(
    session: AsyncSession,
    *,
    period_start: datetime,
    period_end: datetime,
) -> list[TournamentSourcePhoto]:
    by_photo_id: dict[int, TournamentSourcePhoto] = {}

    history_stmt = (
        select(ChannelHistory.id, ChannelHistory.photo_id, ChannelHistory.published_at)
        .where(
            ChannelHistory.photo_id.is_not(None),
            ChannelHistory.published_at >= period_start,
            ChannelHistory.published_at < period_end,
        )
        .order_by(ChannelHistory.published_at.asc(), ChannelHistory.id.asc())
    )
    for history_id, photo_id, published_at in (await session.execute(history_stmt)).all():
        if photo_id is None or published_at is None:
            continue
        by_photo_id.setdefault(
            photo_id,
            TournamentSourcePhoto(
                photo_id=photo_id,
                published_at=published_at,
                source_channel_history_id=history_id,
            ),
        )

    post_stmt = (
        select(Post.id, Post.photo_id, Post.schedule_time)
        .where(
            Post.status == PostStatus.PUBLISHED,
            Post.photo_id.is_not(None),
            Post.schedule_time >= period_start,
            Post.schedule_time < period_end,
        )
        .order_by(Post.schedule_time.asc(), Post.id.asc())
    )
    for post_id, photo_id, schedule_time in (await session.execute(post_stmt)).all():
        if photo_id is None or schedule_time is None:
            continue
        by_photo_id.setdefault(
            photo_id,
            TournamentSourcePhoto(
                photo_id=photo_id,
                published_at=schedule_time,
                source_post_id=post_id,
            ),
        )

    return sorted(by_photo_id.values(), key=lambda item: (item.published_at, item.photo_id))


async def _get_tournament_by_period(
    session: AsyncSession,
    *,
    tournament_type: str,
    period_start: datetime,
    period_end: datetime,
) -> PhotoTournament | None:
    return await session.scalar(
        select(PhotoTournament).where(
            PhotoTournament.type == tournament_type,
            PhotoTournament.period_start == period_start,
            PhotoTournament.period_end == period_end,
        )
    )


async def _used_weekly_tournament_ids(session: AsyncSession) -> set[int]:
    stmt = (
        select(PhotoTournamentEntry.source_weekly_tournament_id)
        .join(PhotoTournament, PhotoTournament.id == PhotoTournamentEntry.tournament_id)
        .where(
            PhotoTournament.type == TOURNAMENT_MONTHLY,
            PhotoTournamentEntry.source_weekly_tournament_id.is_not(None),
        )
    )
    return {weekly_id for weekly_id in (await session.execute(stmt)).scalars() if weekly_id is not None}


async def _weekly_finalist_entries(
    session: AsyncSession,
    weekly_tournament_id: int,
) -> list[PhotoTournamentEntry]:
    final_round = await session.scalar(
        select(PhotoTournamentRound)
        .where(PhotoTournamentRound.tournament_id == weekly_tournament_id)
        .order_by(PhotoTournamentRound.round_number.desc())
        .limit(1)
    )
    if final_round is None:
        return []

    final_match = await session.scalar(
        select(PhotoTournamentMatch)
        .where(
            PhotoTournamentMatch.round_id == final_round.id,
            PhotoTournamentMatch.right_entry_id.is_not(None),
        )
        .order_by(PhotoTournamentMatch.match_number.asc())
        .limit(1)
    )
    if final_match is None or final_match.right_entry_id is None:
        return []

    left_entry = await session.get(PhotoTournamentEntry, final_match.left_entry_id)
    right_entry = await session.get(PhotoTournamentEntry, final_match.right_entry_id)
    return [entry for entry in (left_entry, right_entry) if entry is not None]


async def get_tournament(session: AsyncSession, tournament_id: int) -> PhotoTournament | None:
    return await session.get(PhotoTournament, tournament_id)


async def get_current_tournament(session: AsyncSession) -> PhotoTournament | None:
    return await session.scalar(
        select(PhotoTournament)
        .where(PhotoTournament.status == TOURNAMENT_RUNNING)
        .order_by(PhotoTournament.started_at.desc(), PhotoTournament.id.desc())
        .limit(1)
    )


async def get_latest_completed_tournament(session: AsyncSession) -> PhotoTournament | None:
    return await session.scalar(
        select(PhotoTournament)
        .where(
            PhotoTournament.status == TOURNAMENT_COMPLETED,
            PhotoTournament.winner_photo_id.is_not(None),
        )
        .order_by(PhotoTournament.completed_at.desc(), PhotoTournament.id.desc())
        .limit(1)
    )


async def get_next_open_match_for_user(
    session: AsyncSession,
    *,
    user_id: int,
    tournament_id: int | None = None,
) -> TournamentMatchView | None:
    current = now_in_app_tz()
    user_vote_exists = exists().where(
        PhotoTournamentVote.match_id == PhotoTournamentMatch.id,
        PhotoTournamentVote.user_id == user_id,
    )
    stmt = (
        select(PhotoTournamentMatch)
        .join(PhotoTournament, PhotoTournament.id == PhotoTournamentMatch.tournament_id)
        .join(PhotoTournamentRound, PhotoTournamentRound.id == PhotoTournamentMatch.round_id)
        .options(
            selectinload(PhotoTournamentMatch.tournament),
            selectinload(PhotoTournamentMatch.round),
            selectinload(PhotoTournamentMatch.left_entry).selectinload(PhotoTournamentEntry.photo),
            selectinload(PhotoTournamentMatch.right_entry).selectinload(PhotoTournamentEntry.photo),
        )
        .where(
            PhotoTournament.status == TOURNAMENT_RUNNING,
            PhotoTournament.voting_ends_at.is_not(None),
            PhotoTournamentMatch.status == MATCH_OPEN,
            ~user_vote_exists,
        )
        .order_by(
            PhotoTournament.started_at.desc(),
            PhotoTournamentMatch.tournament_id.desc(),
            PhotoTournamentRound.round_number.asc(),
            PhotoTournamentMatch.match_number.asc(),
        )
    )
    if tournament_id is not None:
        stmt = stmt.where(PhotoTournamentMatch.tournament_id == tournament_id)

    for match in (await session.execute(stmt)).scalars():
        if match.feeder_left_match_id is None and match.right_entry_id is None:
            continue
        tournament = match.tournament
        if tournament is None or not _voting_is_open(tournament, now=current):
            continue
        view = await resolve_user_match_view(session, user_id=user_id, match=match)
        if view is not None:
            return view
    return None


async def get_user_tournament_champion_entry(
    session: AsyncSession,
    *,
    user_id: int,
    tournament_id: int,
) -> PhotoTournamentEntry | None:
    final_round = await session.scalar(
        select(PhotoTournamentRound)
        .where(PhotoTournamentRound.tournament_id == tournament_id)
        .order_by(PhotoTournamentRound.round_number.desc())
        .limit(1)
    )
    if final_round is None:
        return None

    final_match = await session.scalar(
        select(PhotoTournamentMatch)
        .where(PhotoTournamentMatch.round_id == final_round.id)
        .order_by(PhotoTournamentMatch.match_number.asc())
        .limit(1)
    )
    if final_match is None:
        return None

    chosen_entry_id = await session.scalar(
        select(PhotoTournamentVote.chosen_entry_id).where(
            PhotoTournamentVote.match_id == final_match.id,
            PhotoTournamentVote.user_id == user_id,
        )
    )
    if chosen_entry_id is None:
        return None
    return await _entry_with_photo(session, chosen_entry_id)


