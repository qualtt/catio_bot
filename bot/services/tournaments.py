from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BufferedInputFile
from PIL import Image, ImageDraw, ImageOps
from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.config import config
from bot.content import bot_content
from bot.keyboards.inline import get_tournament_start_kb
from bot.services.photo_storage import download_photo
from db.crud import combine_slot, ensure_app_timezone, now_in_app_tz
from db.database import async_session
from db.models.channel_history import ChannelHistory
from db.models.photo_tournament import (
    ENTRY_ACTIVE,
    ENTRY_ELIMINATED,
    ENTRY_WINNER,
    MATCH_BYE,
    MATCH_CLOSED,
    MATCH_OPEN,
    NOTIFICATION_FAILED,
    NOTIFICATION_SENT,
    ROUND_CLOSED,
    ROUND_OPEN,
    TOURNAMENT_CANCELLED,
    TOURNAMENT_COMPLETED,
    TOURNAMENT_MONTHLY,
    TOURNAMENT_RUNNING,
    TOURNAMENT_WEEKLY,
    PhotoTournament,
    PhotoTournamentEntry,
    PhotoTournamentMatch,
    PhotoTournamentNotification,
    PhotoTournamentRound,
    PhotoTournamentVote,
)
from db.models.post import Post, PostStatus
from db.models.user import User


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TournamentSourcePhoto:
    photo_id: int
    published_at: datetime
    source_post_id: int | None = None
    source_channel_history_id: int | None = None


@dataclass(frozen=True)
class TournamentVoteSubmission:
    accepted: bool
    created: bool
    tournament_id: int | None = None


@dataclass(frozen=True)
class TournamentMatchView:
    match: PhotoTournamentMatch
    left_entry: PhotoTournamentEntry
    right_entry: PhotoTournamentEntry


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


def last_completed_week_period(now: datetime | None = None) -> tuple[datetime, datetime]:
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


def _voting_is_open(tournament: PhotoTournament, *, now: datetime | None = None) -> bool:
    if tournament.voting_ends_at is None:
        return False
    current = ensure_app_timezone(now or now_in_app_tz())
    voting_ends = ensure_app_timezone(tournament.voting_ends_at)
    return voting_ends > current


async def _create_full_bracket(
    session: AsyncSession,
    tournament: PhotoTournament,
    entries: list[PhotoTournamentEntry],
    *,
    now: datetime,
) -> None:
    voting_ends = now + round_duration()
    tournament.status = TOURNAMENT_RUNNING
    tournament.started_at = tournament.started_at or now
    tournament.voting_ends_at = voting_ends
    round_count = _bracket_round_count(len(entries))
    tournament.current_round_number = round_count

    round_items: list[PhotoTournamentRound] = []
    for round_number in range(1, round_count + 1):
        round_item = PhotoTournamentRound(
            tournament_id=tournament.id,
            round_number=round_number,
            status=ROUND_OPEN,
            started_at=now,
            ends_at=voting_ends,
        )
        session.add(round_item)
        round_items.append(round_item)
    await session.flush()

    first_round = round_items[0]
    current_round_matches: list[PhotoTournamentMatch] = []
    for match_index, entry_index in enumerate(range(0, len(entries), 2), start=1):
        left_entry = entries[entry_index]
        right_entry = entries[entry_index + 1] if entry_index + 1 < len(entries) else None
        match = PhotoTournamentMatch(
            tournament_id=tournament.id,
            round_id=first_round.id,
            match_number=match_index,
            left_entry_id=left_entry.id,
            right_entry_id=right_entry.id if right_entry else None,
            winner_entry_id=left_entry.id if right_entry is None else None,
            status=MATCH_BYE if right_entry is None else MATCH_OPEN,
        )
        session.add(match)
        current_round_matches.append(match)
    await session.flush()

    for round_number in range(2, round_count + 1):
        round_item = round_items[round_number - 1]
        next_round_matches: list[PhotoTournamentMatch] = []
        for match_index in range(0, len(current_round_matches), 2):
            left_feeder = current_round_matches[match_index]
            right_feeder = (
                current_round_matches[match_index + 1]
                if match_index + 1 < len(current_round_matches)
                else None
            )
            match = PhotoTournamentMatch(
                tournament_id=tournament.id,
                round_id=round_item.id,
                match_number=match_index // 2 + 1,
                feeder_left_match_id=left_feeder.id,
                feeder_right_match_id=right_feeder.id if right_feeder else None,
                status=MATCH_OPEN,
            )
            session.add(match)
            next_round_matches.append(match)
        await session.flush()
        current_round_matches = next_round_matches


async def create_weekly_tournament_if_due(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> PhotoTournament | None:
    if not config.PHOTO_TOURNAMENTS_ENABLED:
        return None

    current = ensure_app_timezone(now or now_in_app_tz())
    period_start, period_end = last_completed_week_period(current)
    if current < weekly_notification_time(period_end):
        return None

    existing = await _get_tournament_by_period(
        session,
        tournament_type=TOURNAMENT_WEEKLY,
        period_start=period_start,
        period_end=period_end,
    )
    if existing is not None:
        return existing

    sources = await collect_weekly_source_photos(session, period_start=period_start, period_end=period_end)
    tournament = PhotoTournament(
        type=TOURNAMENT_WEEKLY,
        period_start=period_start,
        period_end=period_end,
        status=TOURNAMENT_RUNNING,
        current_round_number=0,
        started_at=current,
    )
    session.add(tournament)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        return await _get_tournament_by_period(
            session,
            tournament_type=TOURNAMENT_WEEKLY,
            period_start=period_start,
            period_end=period_end,
        )

    if len(sources) < max(config.PHOTO_TOURNAMENT_MIN_ENTRIES, 2):
        tournament.status = TOURNAMENT_CANCELLED
        tournament.completed_at = current
        await session.commit()
        return tournament

    entries = []
    for seed, source in enumerate(sources, start=1):
        entry = PhotoTournamentEntry(
            tournament_id=tournament.id,
            photo_id=source.photo_id,
            source_post_id=source.source_post_id,
            source_channel_history_id=source.source_channel_history_id,
            seed=seed,
            status=ENTRY_ACTIVE,
        )
        session.add(entry)
        entries.append(entry)
    await session.flush()
    await _create_full_bracket(session, tournament, entries, now=current)
    await session.commit()
    await session.refresh(tournament)
    return tournament


def _entry_pair_winner(
    left_entry: PhotoTournamentEntry,
    right_entry: PhotoTournamentEntry | None,
    *,
    left_votes: int,
    right_votes: int,
) -> PhotoTournamentEntry:
    if right_entry is None:
        return left_entry
    if left_votes > right_votes:
        return left_entry
    if right_votes > left_votes:
        return right_entry
    return min(
        (left_entry, right_entry),
        key=lambda entry: (entry.seed, entry.id),
    )


def _match_winner(match: PhotoTournamentMatch) -> PhotoTournamentEntry:
    if match.right_entry is None:
        return match.left_entry
    return _entry_pair_winner(
        match.left_entry,
        match.right_entry,
        left_votes=match.left_votes,
        right_votes=match.right_votes,
    )


async def _vote_counts_for_match(
    session: AsyncSession,
    *,
    match_id: int,
    left_entry_id: int,
    right_entry_id: int,
) -> tuple[int, int]:
    rows = (
        await session.execute(
            select(PhotoTournamentVote.chosen_entry_id, func.count())
            .where(
                PhotoTournamentVote.match_id == match_id,
                PhotoTournamentVote.chosen_entry_id.in_((left_entry_id, right_entry_id)),
            )
            .group_by(PhotoTournamentVote.chosen_entry_id)
        )
    ).all()
    counts = {entry_id: count for entry_id, count in rows}
    return counts.get(left_entry_id, 0), counts.get(right_entry_id, 0)


async def _favorite_photo_id(
    session: AsyncSession,
    *,
    final_round_id: int,
) -> int | None:
    row = (
        await session.execute(
            select(PhotoTournamentEntry.photo_id, func.count())
            .join(PhotoTournamentVote, PhotoTournamentVote.chosen_entry_id == PhotoTournamentEntry.id)
            .join(PhotoTournamentMatch, PhotoTournamentMatch.id == PhotoTournamentVote.match_id)
            .where(PhotoTournamentMatch.round_id == final_round_id)
            .group_by(PhotoTournamentEntry.photo_id)
            .order_by(func.count().desc(), PhotoTournamentEntry.photo_id.asc())
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    return row[0]


async def _close_tournament(
    session: AsyncSession,
    tournament: PhotoTournament,
    *,
    now: datetime,
) -> None:
    rounds = list(
        (
            await session.execute(
                select(PhotoTournamentRound)
                .options(
                    selectinload(PhotoTournamentRound.matches).selectinload(PhotoTournamentMatch.left_entry),
                    selectinload(PhotoTournamentRound.matches).selectinload(PhotoTournamentMatch.right_entry),
                )
                .where(PhotoTournamentRound.tournament_id == tournament.id)
                .order_by(PhotoTournamentRound.round_number.asc())
            )
        ).scalars()
    )
    match_by_id: dict[int, PhotoTournamentMatch] = {}
    for round_item in rounds:
        for match in round_item.matches:
            match_by_id[match.id] = match

    final_match: PhotoTournamentMatch | None = None

    for round_item in rounds:
        for match in sorted(round_item.matches, key=lambda item: item.match_number):
            if match.winner_entry_id is not None:
                continue

            if match.feeder_left_match_id is None:
                if match.status == MATCH_BYE:
                    winner = match.left_entry
                else:
                    winner = _match_winner(match)
                match.winner_entry_id = winner.id
                match.status = MATCH_CLOSED
                match.closed_at = now
                loser = match.right_entry
                if loser is not None and loser.id != winner.id:
                    loser.status = ENTRY_ELIMINATED
                if round_item.round_number == rounds[-1].round_number:
                    final_match = match
                continue

            left_feeder = match_by_id[match.feeder_left_match_id]
            right_feeder = (
                match_by_id[match.feeder_right_match_id]
                if match.feeder_right_match_id is not None
                else None
            )
            left_entry = await session.get(PhotoTournamentEntry, left_feeder.winner_entry_id)
            right_entry = (
                await session.get(PhotoTournamentEntry, right_feeder.winner_entry_id)
                if right_feeder is not None and right_feeder.winner_entry_id is not None
                else None
            )
            match.left_entry_id = left_entry.id if left_entry is not None else None
            match.right_entry_id = right_entry.id if right_entry is not None else None

            if right_entry is None:
                winner = left_entry
                match.winner_entry_id = winner.id if winner is not None else None
                match.status = MATCH_BYE if winner is not None else MATCH_CLOSED
            else:
                left_votes, right_votes = await _vote_counts_for_match(
                    session,
                    match_id=match.id,
                    left_entry_id=left_entry.id,
                    right_entry_id=right_entry.id,
                )
                match.left_votes = left_votes
                match.right_votes = right_votes
                winner = _entry_pair_winner(
                    left_entry,
                    right_entry,
                    left_votes=left_votes,
                    right_votes=right_votes,
                )
                match.winner_entry_id = winner.id
                match.status = MATCH_CLOSED
                loser = right_entry if winner.id == left_entry.id else left_entry
                if loser.id != winner.id:
                    loser.status = ENTRY_ELIMINATED

            match.closed_at = now
            final_match = match

        round_item.status = ROUND_CLOSED
        round_item.closed_at = now

    if final_match is None or final_match.winner_entry_id is None:
        tournament.status = TOURNAMENT_CANCELLED
        tournament.completed_at = now
        return

    winner_entry = await session.get(PhotoTournamentEntry, final_match.winner_entry_id)
    if winner_entry is not None:
        winner_entry.status = ENTRY_WINNER
        tournament.winner_photo_id = winner_entry.photo_id

    tournament.favorite_photo_id = await _favorite_photo_id(session, final_round_id=rounds[-1].id)
    tournament.status = TOURNAMENT_COMPLETED
    tournament.completed_at = now


async def close_due_tournaments(session: AsyncSession, *, now: datetime | None = None) -> int:
    current = ensure_app_timezone(now or now_in_app_tz())
    tournaments = list(
        (
            await session.execute(
                select(PhotoTournament).where(
                    PhotoTournament.status == TOURNAMENT_RUNNING,
                    PhotoTournament.voting_ends_at.is_not(None),
                )
                .order_by(PhotoTournament.voting_ends_at.asc(), PhotoTournament.id.asc())
            )
        ).scalars()
    )
    closed_tournaments = [
        tournament
        for tournament in tournaments
        if ensure_app_timezone(tournament.voting_ends_at) <= current
    ]
    for tournament in closed_tournaments:
        await _close_tournament(session, tournament, now=current)
    if closed_tournaments:
        await session.commit()
    return len(closed_tournaments)


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


async def create_monthly_tournament_if_due(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> PhotoTournament | None:
    if not config.PHOTO_TOURNAMENTS_ENABLED:
        return None

    used_weekly_ids = await _used_weekly_tournament_ids(session)
    stmt = (
        select(PhotoTournament)
        .where(
            PhotoTournament.type == TOURNAMENT_WEEKLY,
            PhotoTournament.status == TOURNAMENT_COMPLETED,
        )
        .order_by(PhotoTournament.period_end.asc(), PhotoTournament.id.asc())
    )
    if used_weekly_ids:
        stmt = stmt.where(PhotoTournament.id.notin_(used_weekly_ids))

    weeklies = list((await session.execute(stmt.limit(max(config.PHOTO_TOURNAMENT_MONTHLY_WEEKS, 1)))).scalars())
    if len(weeklies) < max(config.PHOTO_TOURNAMENT_MONTHLY_WEEKS, 1):
        return None

    period_start = weeklies[0].period_start
    period_end = weeklies[-1].period_end
    existing = await _get_tournament_by_period(
        session,
        tournament_type=TOURNAMENT_MONTHLY,
        period_start=period_start,
        period_end=period_end,
    )
    if existing is not None:
        return existing

    current = ensure_app_timezone(now or now_in_app_tz())
    tournament = PhotoTournament(
        type=TOURNAMENT_MONTHLY,
        period_start=period_start,
        period_end=period_end,
        status=TOURNAMENT_RUNNING,
        current_round_number=0,
        started_at=current,
    )
    session.add(tournament)
    await session.flush()

    entries: list[PhotoTournamentEntry] = []
    seen_photo_ids: set[int] = set()
    seed = 1
    for weekly in weeklies:
        finalists = await _weekly_finalist_entries(session, weekly.id)
        for finalist in finalists:
            if finalist.photo_id in seen_photo_ids:
                continue
            seen_photo_ids.add(finalist.photo_id)
            entry = PhotoTournamentEntry(
                tournament_id=tournament.id,
                photo_id=finalist.photo_id,
                source_weekly_tournament_id=weekly.id,
                seed=seed,
                status=ENTRY_ACTIVE,
            )
            session.add(entry)
            entries.append(entry)
            seed += 1

    if len(entries) < max(config.PHOTO_TOURNAMENT_MIN_ENTRIES, 2):
        tournament.status = TOURNAMENT_CANCELLED
        tournament.completed_at = current
        await session.commit()
        return tournament

    await session.flush()
    await _create_full_bracket(session, tournament, entries, now=current)
    await session.commit()
    await session.refresh(tournament)
    return tournament


async def send_tournament_notifications(
    bot: Bot,
    session: AsyncSession,
    tournament: PhotoTournament,
) -> int:
    if tournament.status != TOURNAMENT_RUNNING or tournament.notification_sent_at is not None:
        return 0

    entry_count = await session.scalar(
        select(func.count(PhotoTournamentEntry.id)).where(PhotoTournamentEntry.tournament_id == tournament.id)
    ) or 0

    notified_user_ids = {
        user_id
        for user_id in (
            await session.execute(
                select(PhotoTournamentNotification.user_id).where(
                    PhotoTournamentNotification.tournament_id == tournament.id,
                )
            )
        ).scalars()
    }
    users = list((await session.execute(select(User).order_by(User.id.asc()))).scalars())
    sent_count = 0
    message_key = (
        "tournament_monthly_invite"
        if tournament.type == TOURNAMENT_MONTHLY
        else "tournament_weekly_invite"
    )

    for user in users:
        if user.id in notified_user_ids:
            continue
        try:
            sent = await bot.send_message(
                chat_id=user.telegram_id,
                text=bot_content.message(
                    message_key,
                    period=tournament_period_label(tournament),
                    count=entry_count,
                    voting_deadline=tournament_voting_deadline_label(tournament),
                ),
                reply_markup=get_tournament_start_kb(tournament.id),
            )
            notification = PhotoTournamentNotification(
                tournament_id=tournament.id,
                user_id=user.id,
                telegram_message_id=sent.message_id,
                status=NOTIFICATION_SENT,
            )
            sent_count += 1
        except TelegramAPIError as error:
            notification = PhotoTournamentNotification(
                tournament_id=tournament.id,
                user_id=user.id,
                status=NOTIFICATION_FAILED,
                error_message=str(error)[:500],
            )
        session.add(notification)

    tournament.notification_sent_at = now_in_app_tz()
    await session.commit()
    return sent_count


async def run_tournament_maintenance(bot: Bot) -> None:
    if not config.PHOTO_TOURNAMENTS_ENABLED:
        return

    async with async_session() as session:
        await create_weekly_tournament_if_due(session)
        closed_count = await close_due_tournaments(session)
        if closed_count:
            logger.info("Closed %s photo tournaments", closed_count)
        await create_monthly_tournament_if_due(session)

        tournaments_to_notify = list(
            (
                await session.execute(
                    select(PhotoTournament)
                    .where(
                        PhotoTournament.status == TOURNAMENT_RUNNING,
                        PhotoTournament.notification_sent_at.is_(None),
                    )
                    .order_by(PhotoTournament.started_at.asc(), PhotoTournament.id.asc())
                )
            ).scalars()
        )
        for tournament in tournaments_to_notify:
            sent_count = await send_tournament_notifications(bot, session, tournament)
            if sent_count:
                logger.info("Sent %s notifications for photo tournament %s", sent_count, tournament.id)


async def get_tournament(session: AsyncSession, tournament_id: int) -> PhotoTournament | None:
    return await session.get(PhotoTournament, tournament_id)


async def get_current_tournament(session: AsyncSession) -> PhotoTournament | None:
    return await session.scalar(
        select(PhotoTournament)
        .where(PhotoTournament.status == TOURNAMENT_RUNNING)
        .order_by(PhotoTournament.started_at.desc(), PhotoTournament.id.desc())
        .limit(1)
    )


async def _user_match_choice_entry(
    session: AsyncSession,
    *,
    user_id: int,
    match: PhotoTournamentMatch,
) -> PhotoTournamentEntry | None:
    if match.status == MATCH_BYE:
        return match.left_entry
    if match.winner_entry_id is not None:
        return match.winner_entry
    chosen_entry_id = await session.scalar(
        select(PhotoTournamentVote.chosen_entry_id).where(
            PhotoTournamentVote.match_id == match.id,
            PhotoTournamentVote.user_id == user_id,
        )
    )
    if chosen_entry_id is None:
        return None
    return await session.get(PhotoTournamentEntry, chosen_entry_id)


async def resolve_user_match_view(
    session: AsyncSession,
    *,
    user_id: int,
    match: PhotoTournamentMatch,
) -> TournamentMatchView | None:
    if match.feeder_left_match_id is None:
        if match.left_entry is None or match.right_entry is None:
            return None
        return TournamentMatchView(match=match, left_entry=match.left_entry, right_entry=match.right_entry)

    left_feeder = await session.get(PhotoTournamentMatch, match.feeder_left_match_id)
    if left_feeder is None:
        return None
    left_entry = await _user_match_choice_entry(session, user_id=user_id, match=left_feeder)
    if left_entry is None:
        return None

    if match.feeder_right_match_id is None:
        return None

    right_feeder = await session.get(PhotoTournamentMatch, match.feeder_right_match_id)
    if right_feeder is None:
        return None
    right_entry = await _user_match_choice_entry(session, user_id=user_id, match=right_feeder)
    if right_entry is None:
        return None

    return TournamentMatchView(match=match, left_entry=left_entry, right_entry=right_entry)


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


async def submit_tournament_vote(
    session: AsyncSession,
    *,
    match_id: int,
    chosen_entry_id: int,
    user_id: int,
) -> TournamentVoteSubmission:
    match = await session.scalar(
        select(PhotoTournamentMatch)
        .options(
            selectinload(PhotoTournamentMatch.tournament),
            selectinload(PhotoTournamentMatch.left_entry),
            selectinload(PhotoTournamentMatch.right_entry),
        )
        .where(PhotoTournamentMatch.id == match_id)
        .with_for_update()
    )
    if match is None or match.status != MATCH_OPEN:
        return TournamentVoteSubmission(accepted=False, created=False)

    tournament = match.tournament
    if tournament is None or tournament.status != TOURNAMENT_RUNNING or not _voting_is_open(tournament):
        return TournamentVoteSubmission(accepted=False, created=False)

    view = await resolve_user_match_view(session, user_id=user_id, match=match)
    if view is None or chosen_entry_id not in {view.left_entry.id, view.right_entry.id}:
        return TournamentVoteSubmission(accepted=False, created=False)

    existing = await session.scalar(
        select(PhotoTournamentVote).where(
            PhotoTournamentVote.match_id == match_id,
            PhotoTournamentVote.user_id == user_id,
        )
    )
    if existing is not None:
        return TournamentVoteSubmission(
            accepted=True,
            created=False,
            tournament_id=match.tournament_id,
        )

    vote = PhotoTournamentVote(
        tournament_id=match.tournament_id,
        match_id=match.id,
        user_id=user_id,
        chosen_entry_id=chosen_entry_id,
    )
    session.add(vote)
    if match.feeder_left_match_id is None:
        if chosen_entry_id == view.left_entry.id:
            match.left_votes += 1
        else:
            match.right_votes += 1

    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return TournamentVoteSubmission(
            accepted=True,
            created=False,
            tournament_id=match.tournament_id,
        )

    return TournamentVoteSubmission(
        accepted=True,
        created=True,
        tournament_id=match.tournament_id,
    )


def _fit_photo_panel(data: bytes, *, size: tuple[int, int]) -> Image.Image:
    with Image.open(io.BytesIO(data)) as image:
        image = ImageOps.exif_transpose(image).convert("RGB")
        fitted = ImageOps.contain(image, size, Image.Resampling.LANCZOS)

    panel = Image.new("RGB", size, "#111111")
    offset = ((size[0] - fitted.width) // 2, (size[1] - fitted.height) // 2)
    panel.paste(fitted, offset)
    return panel


def _compose_match_image(left_data: bytes, right_data: bytes) -> bytes:
    panel_size = (560, 760)
    gap = 24
    margin = 28
    label_height = 44
    canvas_size = (panel_size[0] * 2 + gap + margin * 2, panel_size[1] + label_height + margin * 2)
    canvas = Image.new("RGB", canvas_size, "#202124")
    draw = ImageDraw.Draw(canvas)

    positions = [
        (margin, margin + label_height),
        (margin + panel_size[0] + gap, margin + label_height),
    ]
    for label, data, position in (("1", left_data, positions[0]), ("2", right_data, positions[1])):
        label_box = (position[0], margin, position[0] + panel_size[0], margin + label_height - 8)
        draw.rounded_rectangle(label_box, radius=8, fill="#ffffff")
        draw.text((label_box[0] + 16, label_box[1] + 10), label, fill="#111111")
        canvas.paste(_fit_photo_panel(data, size=panel_size), position)

    output = io.BytesIO()
    canvas.save(output, format="JPEG", quality=88, optimize=True)
    return output.getvalue()


async def tournament_match_photo_input(view: TournamentMatchView) -> BufferedInputFile:
    left_photo = view.left_entry.photo
    right_photo = view.right_entry.photo
    left_data = await download_photo(
        storage_bucket=left_photo.storage_bucket,
        storage_key=left_photo.storage_key,
    )
    right_data = await download_photo(
        storage_bucket=right_photo.storage_bucket,
        storage_key=right_photo.storage_key,
    )
    return BufferedInputFile(
        _compose_match_image(left_data, right_data),
        filename=f"tournament-{view.match.id}.jpg",
    )
