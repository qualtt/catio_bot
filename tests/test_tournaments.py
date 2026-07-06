from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select

from bot.services.tournaments import (
    close_due_tournaments,
    create_weekly_tournament_if_due,
    get_next_open_match_for_user,
    resolve_user_match_view,
    submit_tournament_vote,
)
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
    assert tournament.voting_ends_at is not None
    assert tournament.current_round_number == 1
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
async def test_create_weekly_tournament_builds_full_bracket_for_four_photos(db_session):
    tz = app_timezone()
    now = datetime(2026, 7, 6, 12, 0, tzinfo=tz)
    photos = [_photo(index) for index in range(1, 5)]
    db_session.add_all(photos)
    await db_session.flush()
    db_session.add_all(
        [
            ChannelHistory(
                message_id=100 + index,
                file_id=f"file-{index}",
                photo_id=photo.id,
                published_at=now - timedelta(days=index),
            )
            for index, photo in enumerate(photos, start=1)
        ]
    )
    await db_session.commit()

    tournament = await create_weekly_tournament_if_due(db_session, now=now)

    assert tournament is not None
    assert tournament.current_round_number == 2
    round_count = await db_session.scalar(
        select(func.count(PhotoTournamentRound.id)).where(PhotoTournamentRound.tournament_id == tournament.id)
    )
    match_count = await db_session.scalar(
        select(func.count(PhotoTournamentMatch.id)).where(PhotoTournamentMatch.tournament_id == tournament.id)
    )
    assert round_count == 2
    assert match_count == 3


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
async def test_close_due_tournament_completes_two_photo_tournament(db_session):
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
    tournament.voting_ends_at = now - timedelta(minutes=1)
    await db_session.commit()

    closed_count = await close_due_tournaments(db_session, now=now)

    refreshed_match = await db_session.get(PhotoTournamentMatch, match.id)
    winner_entry = await db_session.get(PhotoTournamentEntry, match.left_entry_id)
    await db_session.refresh(tournament)
    assert closed_count == 1
    assert refreshed_match.status == MATCH_CLOSED
    assert tournament.status == TOURNAMENT_COMPLETED
    assert tournament.winner_photo_id == first_photo.id
    assert tournament.favorite_photo_id is None
    assert winner_entry.status == ENTRY_WINNER


@pytest.mark.asyncio
async def test_user_advances_to_next_round_after_first_round_vote(db_session):
    tz = app_timezone()
    now = datetime(2026, 7, 6, 12, 0, tzinfo=tz)
    photos = [_photo(index) for index in range(1, 5)]
    user = User(telegram_id=10, username="u", full_name="User")
    db_session.add_all([*photos, user])
    await db_session.flush()
    db_session.add_all(
        [
            ChannelHistory(
                message_id=100 + index,
                file_id=f"file-{index}",
                photo_id=photo.id,
                published_at=now - timedelta(days=index),
            )
            for index, photo in enumerate(photos, start=1)
        ]
    )
    await db_session.commit()
    tournament = await create_weekly_tournament_if_due(db_session, now=now)
    first_round_matches = list(
        (
            await db_session.execute(
                select(PhotoTournamentMatch)
                .join(PhotoTournamentRound, PhotoTournamentRound.id == PhotoTournamentMatch.round_id)
                .where(
                    PhotoTournamentMatch.tournament_id == tournament.id,
                    PhotoTournamentRound.round_number == 1,
                )
                .order_by(PhotoTournamentMatch.match_number)
            )
        ).scalars()
    )

    for match in first_round_matches:
        await submit_tournament_vote(
            db_session,
            match_id=match.id,
            chosen_entry_id=match.left_entry_id,
            user_id=user.id,
        )

    next_view = await get_next_open_match_for_user(
        db_session,
        user_id=user.id,
        tournament_id=tournament.id,
    )
    assert next_view is not None
    assert next_view.match.round.round_number == 2
    assert next_view.left_entry.id == first_round_matches[0].left_entry_id
    assert next_view.right_entry.id == first_round_matches[1].left_entry_id


@pytest.mark.asyncio
async def test_close_due_tournament_sets_favorite_from_final_votes(db_session):
    tz = app_timezone()
    now = datetime(2026, 7, 6, 12, 0, tzinfo=tz)
    photos = [_photo(index) for index in range(1, 5)]
    users = [
        User(telegram_id=10, username="u1", full_name="User 1"),
        User(telegram_id=11, username="u2", full_name="User 2"),
        User(telegram_id=12, username="u3", full_name="User 3"),
    ]
    db_session.add_all([*photos, *users])
    await db_session.flush()
    db_session.add_all(
        [
            ChannelHistory(
                message_id=100 + index,
                file_id=f"file-{index}",
                photo_id=photo.id,
                published_at=now - timedelta(days=index),
            )
            for index, photo in enumerate(photos, start=1)
        ]
    )
    await db_session.commit()
    tournament = await create_weekly_tournament_if_due(db_session, now=now)
    first_round_matches = list(
        (
            await db_session.execute(
                select(PhotoTournamentMatch)
                .join(PhotoTournamentRound, PhotoTournamentRound.id == PhotoTournamentMatch.round_id)
                .where(
                    PhotoTournamentMatch.tournament_id == tournament.id,
                    PhotoTournamentRound.round_number == 1,
                )
                .order_by(PhotoTournamentMatch.match_number)
            )
        ).scalars()
    )
    for user in users:
        for match in first_round_matches:
            await submit_tournament_vote(
                db_session,
                match_id=match.id,
                chosen_entry_id=match.left_entry_id,
                user_id=user.id,
            )

    final_match = await db_session.scalar(
        select(PhotoTournamentMatch)
        .join(PhotoTournamentRound, PhotoTournamentRound.id == PhotoTournamentMatch.round_id)
        .where(
            PhotoTournamentMatch.tournament_id == tournament.id,
            PhotoTournamentRound.round_number == 2,
        )
    )
    final_view = await resolve_user_match_view(db_session, user_id=users[0].id, match=final_match)
    await submit_tournament_vote(
        db_session,
        match_id=final_match.id,
        chosen_entry_id=final_view.left_entry.id,
        user_id=users[0].id,
    )
    await submit_tournament_vote(
        db_session,
        match_id=final_match.id,
        chosen_entry_id=final_view.right_entry.id,
        user_id=users[1].id,
    )
    await submit_tournament_vote(
        db_session,
        match_id=final_match.id,
        chosen_entry_id=final_view.right_entry.id,
        user_id=users[2].id,
    )

    for match in first_round_matches:
        match.left_votes = 3
        match.right_votes = 0
    tournament.voting_ends_at = now - timedelta(minutes=1)
    await db_session.commit()

    await close_due_tournaments(db_session, now=now)
    await db_session.refresh(tournament)

    assert tournament.status == TOURNAMENT_COMPLETED
    assert tournament.winner_photo_id == final_view.right_entry.photo_id
    assert tournament.favorite_photo_id == final_view.right_entry.photo_id
