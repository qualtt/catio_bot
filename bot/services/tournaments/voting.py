from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.crud import ensure_app_timezone, now_in_app_tz
from db.models.photo_tournament import (
    MATCH_BYE,
    MATCH_OPEN,
    TOURNAMENT_RUNNING,
    PhotoTournament,
    PhotoTournamentEntry,
    PhotoTournamentMatch,
    PhotoTournamentVote,
)

from .models import TournamentMatchView, TournamentVoteSubmission

logger = logging.getLogger(__name__)


def _voting_is_open(tournament: PhotoTournament, *, now: datetime | None = None) -> bool:
    if tournament.voting_ends_at is None:
        return False
    current = ensure_app_timezone(now or now_in_app_tz())
    voting_ends = ensure_app_timezone(tournament.voting_ends_at)
    return voting_ends > current


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
    tournament_id: int,
) -> int | None:
    row = (
        await session.execute(
            select(PhotoTournamentEntry.photo_id, func.count())
            .join(PhotoTournamentVote, PhotoTournamentVote.chosen_entry_id == PhotoTournamentEntry.id)
            .join(PhotoTournamentMatch, PhotoTournamentMatch.id == PhotoTournamentVote.match_id)
            .where(PhotoTournamentMatch.tournament_id == tournament_id)
            .group_by(PhotoTournamentEntry.photo_id)
            .order_by(func.count().desc(), PhotoTournamentEntry.photo_id.asc())
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    return row[0]


async def _entry_with_photo(
    session: AsyncSession,
    entry_id: int | None,
) -> PhotoTournamentEntry | None:
    if entry_id is None:
        return None
    return await session.scalar(
        select(PhotoTournamentEntry)
        .options(selectinload(PhotoTournamentEntry.photo))
        .where(PhotoTournamentEntry.id == entry_id)
    )


async def _match_with_feeder_entries(
    session: AsyncSession,
    match_id: int,
) -> PhotoTournamentMatch | None:
    return await session.scalar(
        select(PhotoTournamentMatch)
        .options(
            selectinload(PhotoTournamentMatch.left_entry).selectinload(PhotoTournamentEntry.photo),
            selectinload(PhotoTournamentMatch.right_entry).selectinload(PhotoTournamentEntry.photo),
            selectinload(PhotoTournamentMatch.winner_entry).selectinload(PhotoTournamentEntry.photo),
        )
        .where(PhotoTournamentMatch.id == match_id)
    )


async def _user_match_choice_entry(
    session: AsyncSession,
    *,
    user_id: int,
    match: PhotoTournamentMatch,
) -> PhotoTournamentEntry | None:
    if (
        match.feeder_left_match_id is not None
        and match.feeder_right_match_id is None
        and match.status == MATCH_OPEN
    ):
        feeder = await _match_with_feeder_entries(session, match.feeder_left_match_id)
        if feeder is None:
            return None
        return await _user_match_choice_entry(session, user_id=user_id, match=feeder)

    if match.status == MATCH_BYE:
        return await _entry_with_photo(session, match.left_entry_id)
    if match.winner_entry_id is not None:
        return await _entry_with_photo(session, match.winner_entry_id)
    chosen_entry_id = await session.scalar(
        select(PhotoTournamentVote.chosen_entry_id).where(
            PhotoTournamentVote.match_id == match.id,
            PhotoTournamentVote.user_id == user_id,
        )
    )
    if chosen_entry_id is None:
        return None
    return await _entry_with_photo(session, chosen_entry_id)


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

    left_feeder = await _match_with_feeder_entries(session, match.feeder_left_match_id)
    if left_feeder is None:
        return None
    left_entry = await _user_match_choice_entry(session, user_id=user_id, match=left_feeder)
    if left_entry is None:
        return None

    if match.feeder_right_match_id is None:
        return None

    right_feeder = await _match_with_feeder_entries(session, match.feeder_right_match_id)
    if right_feeder is None:
        return None
    right_entry = await _user_match_choice_entry(session, user_id=user_id, match=right_feeder)
    if right_entry is None:
        return None

    return TournamentMatchView(match=match, left_entry=left_entry, right_entry=right_entry)


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
    await session.flush()
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


