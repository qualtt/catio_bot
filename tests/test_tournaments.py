from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from bot.services.tournaments import create_weekly_tournament_if_due, close_due_rounds, submit_tournament_vote
from db.crud import app_timezone
from db.models.channel_history import ChannelHistory
from db.models.photo import Photo
from db.models.photo_tournament import (
    ENTRY_WINNER,
    MATCH_CLOSED,
    TOURNAMENT_COMPLETED,
    TOURNAMENT_RUNNING,
    PhotoTournamentEntry,
    PhotoTournamentMatch,
    PhotoTournamentRound,
)
from db.models.user import User


def _photo(index: int) -> Photo:
    return Photo(
        storage_bucket="bucket",
        storage_key=f"photos/{index}.jpg",
        sha256=f"{index:064x}",
    )


@pytest.mark.asyncio
async def test_create_weekly_tournament_collects_previous_week_photos(db_session):
    tz = app_timezone()
    now = datetime(2026, 7, 6, 12, 0, tzinfo=tz)
    first_photo = _photo(1)
    second_photo = _photo(2)
    db_session.add_all([first_photo, second_photo])
    await db_session.flush()
    db_session.add_all(
        [
            ChannelHistory(
                chat_id=-100123,
                message_id=101,
                file_id="file-1",
                photo_id=first_photo.id,
                published_at=now - timedelta(days=6),
            ),
            ChannelHistory(
                chat_id=-100123,
                message_id=102,
                file_id="file-2",
                photo_id=second_photo.id,
                published_at=now - timedelta(days=1),
            ),
        ]
    )
    await db_session.commit()

    tournament = await create_weekly_tournament_if_due(db_session, now=now)

    assert tournament is not None
    assert tournament.status == TOURNAMENT_RUNNING
    entries = list(
        (
            await db_session.execute(
                select(PhotoTournamentEntry)
                .where(PhotoTournamentEntry.tournament_id == tournament.id)
                .order_by(PhotoTournamentEntry.seed)
            )
        ).scalars()
    )
    assert [entry.photo_id for entry in entries] == [first_photo.id, second_photo.id]
    match = await db_session.scalar(
        select(PhotoTournamentMatch).where(PhotoTournamentMatch.tournament_id == tournament.id)
    )
    assert match.left_entry_id == entries[0].id
    assert match.right_entry_id == entries[1].id


@pytest.mark.asyncio
async def test_submit_tournament_vote_is_idempotent(db_session):
    tz = app_timezone()
    now = datetime(2026, 7, 6, 12, 0, tzinfo=tz)
    first_photo = _photo(1)
    second_photo = _photo(2)
    user = User(telegram_id=10, username="u", full_name="User")
    db_session.add_all([first_photo, second_photo, user])
    await db_session.flush()
    db_session.add_all(
        [
            ChannelHistory(message_id=101, file_id="file-1", photo_id=first_photo.id, published_at=now - timedelta(days=1)),
            ChannelHistory(message_id=102, file_id="file-2", photo_id=second_photo.id, published_at=now - timedelta(days=1)),
        ]
    )
    await db_session.commit()
    tournament = await create_weekly_tournament_if_due(db_session, now=now)
    match = await db_session.scalar(
        select(PhotoTournamentMatch).where(PhotoTournamentMatch.tournament_id == tournament.id)
    )

    first = await submit_tournament_vote(
        db_session,
        match_id=match.id,
        chosen_entry_id=match.left_entry_id,
        user_id=user.id,
    )
    second = await submit_tournament_vote(
        db_session,
        match_id=match.id,
        chosen_entry_id=match.left_entry_id,
        user_id=user.id,
    )

    refreshed_match = await db_session.get(PhotoTournamentMatch, match.id)
    assert first.created is True
    assert second.created is False
    assert refreshed_match.left_votes == 1
    assert refreshed_match.right_votes == 0


@pytest.mark.asyncio
async def test_close_due_round_completes_two_photo_tournament(db_session):
    tz = app_timezone()
    now = datetime(2026, 7, 6, 12, 0, tzinfo=tz)
    first_photo = _photo(1)
    second_photo = _photo(2)
    db_session.add_all([first_photo, second_photo])
    await db_session.flush()
    db_session.add_all(
        [
            ChannelHistory(message_id=101, file_id="file-1", photo_id=first_photo.id, published_at=now - timedelta(days=1)),
            ChannelHistory(message_id=102, file_id="file-2", photo_id=second_photo.id, published_at=now - timedelta(days=1)),
        ]
    )
    await db_session.commit()
    tournament = await create_weekly_tournament_if_due(db_session, now=now)
    match = await db_session.scalar(
        select(PhotoTournamentMatch).where(PhotoTournamentMatch.tournament_id == tournament.id)
    )
    match.left_votes = 2
    round_item = await db_session.get(PhotoTournamentRound, match.round_id)
    round_item.ends_at = now - timedelta(minutes=1)
    await db_session.commit()

    closed_count = await close_due_rounds(db_session, now=now)

    refreshed_match = await db_session.get(PhotoTournamentMatch, match.id)
    winner_entry = await db_session.get(PhotoTournamentEntry, match.left_entry_id)
    await db_session.refresh(tournament)
    assert closed_count == 1
    assert refreshed_match.status == MATCH_CLOSED
    assert tournament.status == TOURNAMENT_COMPLETED
    assert tournament.winner_photo_id == first_photo.id
    assert winner_entry.status == ENTRY_WINNER
