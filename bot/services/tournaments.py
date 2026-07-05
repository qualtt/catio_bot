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


def tournament_type_label(tournament_type: str) -> str:
    if tournament_type == TOURNAMENT_MONTHLY:
        return bot_content.message("tournament_monthly_label")
    return bot_content.message("tournament_weekly_label")


def tournament_period_label(tournament: PhotoTournament) -> str:
    start = ensure_app_timezone(tournament.period_start).strftime("%Y-%m-%d")
    end = ensure_app_timezone(tournament.period_end - timedelta(seconds=1)).strftime("%Y-%m-%d")
    return f"{start} - {end}"


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


async def _create_round(
    session: AsyncSession,
    tournament: PhotoTournament,
    entries: list[PhotoTournamentEntry],
    *,
    round_number: int,
    now: datetime,
) -> PhotoTournamentRound:
    round_item = PhotoTournamentRound(
        tournament_id=tournament.id,
        round_number=round_number,
        status=ROUND_OPEN,
        started_at=now,
        ends_at=now + round_duration(),
    )
    session.add(round_item)
    await session.flush()

    for match_index, entry_index in enumerate(range(0, len(entries), 2), start=1):
        left_entry = entries[entry_index]
        right_entry = entries[entry_index + 1] if entry_index + 1 < len(entries) else None
        match = PhotoTournamentMatch(
            tournament_id=tournament.id,
            round_id=round_item.id,
            match_number=match_index,
            left_entry_id=left_entry.id,
            right_entry_id=right_entry.id if right_entry else None,
            winner_entry_id=left_entry.id if right_entry is None else None,
            status=MATCH_BYE if right_entry is None else MATCH_OPEN,
        )
        session.add(match)

    tournament.status = TOURNAMENT_RUNNING
    tournament.current_round_number = round_number
    tournament.started_at = tournament.started_at or now
    return round_item


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
    await _create_round(session, tournament, entries, round_number=1, now=current)
    await session.commit()
    await session.refresh(tournament)
    return tournament


def _match_winner(match: PhotoTournamentMatch) -> PhotoTournamentEntry:
    if match.right_entry is None:
        return match.left_entry
    if match.left_votes > match.right_votes:
        return match.left_entry
    if match.right_votes > match.left_votes:
        return match.right_entry
    return min(
        (match.left_entry, match.right_entry),
        key=lambda entry: (entry.seed, entry.id),
    )


async def close_due_rounds(session: AsyncSession, *, now: datetime | None = None) -> int:
    current = ensure_app_timezone(now or now_in_app_tz())
    stmt = (
        select(PhotoTournamentRound)
        .options(
            selectinload(PhotoTournamentRound.tournament),
            selectinload(PhotoTournamentRound.matches).selectinload(PhotoTournamentMatch.left_entry),
            selectinload(PhotoTournamentRound.matches).selectinload(PhotoTournamentMatch.right_entry),
        )
        .where(
            PhotoTournamentRound.status == ROUND_OPEN,
            PhotoTournamentRound.ends_at <= current,
        )
        .order_by(PhotoTournamentRound.ends_at.asc(), PhotoTournamentRound.id.asc())
    )
    rounds = list((await session.execute(stmt)).scalars())
    closed_count = 0

    for round_item in rounds:
        tournament = round_item.tournament
        if tournament.status != TOURNAMENT_RUNNING:
            continue

        winners: list[PhotoTournamentEntry] = []
        for match in sorted(round_item.matches, key=lambda item: item.match_number):
            if match.status not in (MATCH_OPEN, MATCH_BYE):
                continue
            winner = _match_winner(match)
            match.winner_entry_id = winner.id
            match.status = MATCH_CLOSED
            match.closed_at = current
            winners.append(winner)

            loser = match.right_entry if winner.id == match.left_entry_id else match.left_entry
            if loser is not None and loser.id != winner.id:
                loser.status = ENTRY_ELIMINATED

        round_item.status = ROUND_CLOSED
        round_item.closed_at = current
        closed_count += 1

        if len(winners) <= 1:
            winner = winners[0] if winners else None
            if winner:
                winner.status = ENTRY_WINNER
                tournament.winner_photo_id = winner.photo_id
            tournament.status = TOURNAMENT_COMPLETED
            tournament.completed_at = current
            continue

        winners.sort(key=lambda entry: (entry.seed, entry.id))
        await _create_round(
            session,
            tournament,
            winners,
            round_number=round_item.round_number + 1,
            now=current,
        )

    if closed_count:
        await session.commit()
    return closed_count


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
    await _create_round(session, tournament, entries, round_number=1, now=current)
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
        closed_count = await close_due_rounds(session)
        if closed_count:
            logger.info("Closed %s photo tournament rounds", closed_count)
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


async def get_next_open_match_for_user(
    session: AsyncSession,
    *,
    user_id: int,
    tournament_id: int | None = None,
) -> PhotoTournamentMatch | None:
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
            PhotoTournamentRound.status == ROUND_OPEN,
            PhotoTournamentMatch.status == MATCH_OPEN,
            PhotoTournamentMatch.right_entry_id.is_not(None),
            ~user_vote_exists,
        )
        .order_by(
            PhotoTournament.started_at.desc(),
            PhotoTournamentMatch.tournament_id.desc(),
            PhotoTournamentRound.round_number.asc(),
            PhotoTournamentMatch.match_number.asc(),
        )
        .limit(1)
    )
    if tournament_id is not None:
        stmt = stmt.where(PhotoTournamentMatch.tournament_id == tournament_id)
    return await session.scalar(stmt)


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
            selectinload(PhotoTournamentMatch.left_entry),
            selectinload(PhotoTournamentMatch.right_entry),
        )
        .where(PhotoTournamentMatch.id == match_id)
        .with_for_update()
    )
    if (
        match is None
        or match.status != MATCH_OPEN
        or match.right_entry_id is None
        or chosen_entry_id not in {match.left_entry_id, match.right_entry_id}
    ):
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
    if chosen_entry_id == match.left_entry_id:
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


async def tournament_match_photo_input(match: PhotoTournamentMatch) -> BufferedInputFile:
    if match.right_entry is None:
        raise ValueError("Tournament match has no right entry")
    left_photo = match.left_entry.photo
    right_photo = match.right_entry.photo
    left_data = await download_photo(
        storage_bucket=left_photo.storage_bucket,
        storage_key=left_photo.storage_key,
    )
    right_data = await download_photo(
        storage_bucket=right_photo.storage_bucket,
        storage_key=right_photo.storage_key,
    )
    return BufferedInputFile(_compose_match_image(left_data, right_data), filename=f"tournament-{match.id}.jpg")
