from __future__ import annotations

import logging
from datetime import datetime

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.config import config
from db.crud import ensure_app_timezone, now_in_app_tz
from db.database import async_session
from db.models.photo_tournament import (
    ENTRY_ACTIVE,
    ENTRY_ELIMINATED,
    ENTRY_WINNER,
    MATCH_BYE,
    MATCH_CLOSED,
    MATCH_OPEN,
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
    PhotoTournamentRound,
)

from .notifications import (
    send_pending_tournament_results_notifications,
    send_tournament_notifications,
)
from .queries import (
    _get_tournament_by_period,
    _used_weekly_tournament_ids,
    _weekly_finalist_entries,
    collect_weekly_source_photos,
)
from .utils import (
    _bracket_round_count,
    last_completed_week_period,
    round_duration,
    weekly_notification_time,
)
from .voting import (
    _entry_pair_winner,
    _favorite_photo_id,
    _match_winner,
    _vote_counts_for_match,
)

logger = logging.getLogger(__name__)


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

            if right_entry is None or left_entry is None:
                winner = left_entry if right_entry is None else right_entry
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

    tournament.favorite_photo_id = await _favorite_photo_id(session, tournament_id=tournament.id)
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

        results_count = await send_pending_tournament_results_notifications(bot, session)
        if results_count:
            logger.info("Sent results notifications for %s photo tournaments", results_count)


