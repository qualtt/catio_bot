from datetime import timedelta

import pytest
from sqlalchemy import select

from bot.services import identification
from db.models.channel_history import ChannelHistory
from db.models.photo import Photo
from db.models.photo_identification import (
    PhotoIdentificationAssignment,
    PhotoIdentificationBatch,
    PhotoIdentificationVote,
)
from db.models.score_event import ScoreEvent
from db.models.user import User


@pytest.mark.asyncio
async def test_submit_identification_vote_queues_single_vote_by_default(db_session, monkeypatch):
    monkeypatch.setattr(identification.config, "IDENTIFICATION_VOTES_REQUIRED", 1)
    monkeypatch.setattr(identification.config, "IDENTIFICATION_CONSENSUS_PERCENT", 67)
    monkeypatch.setattr(identification.config, "IDENTIFICATION_MAX_VOTES_PER_PHOTO", 7)

    user = User(telegram_id=1001, username="one", full_name="One")
    photo = Photo(storage_bucket="bucket", storage_key="photos/1.jpg")
    history = ChannelHistory(message_id=10, file_id="file-1", photo=photo)
    db_session.add_all([user, photo, history])
    await db_session.flush()
    db_session.add(
        PhotoIdentificationAssignment(
            channel_history_id=history.id,
            user_id=user.id,
            status=identification.ASSIGNMENT_ASSIGNED,
            expires_at=identification.now_in_app_tz() + timedelta(minutes=30),
        )
    )
    await db_session.commit()

    result = await identification.submit_identification_vote(db_session, user_id=user.id, animal_type="кот")

    assert result.created is True
    assert result.queued_for_review is True
    assert history.review_status == identification.REVIEW_QUEUED
    assert history.suggested_animal_type == "кот"


@pytest.mark.asyncio
async def test_queue_identification_item_when_consensus_is_reached(db_session, monkeypatch):
    monkeypatch.setattr(identification.config, "IDENTIFICATION_VOTES_REQUIRED", 2)
    monkeypatch.setattr(identification.config, "IDENTIFICATION_CONSENSUS_PERCENT", 66)
    monkeypatch.setattr(identification.config, "IDENTIFICATION_MAX_VOTES_PER_PHOTO", 5)

    users = [
        User(telegram_id=1001, username="one", full_name="One"),
        User(telegram_id=1002, username="two", full_name="Two"),
        User(telegram_id=1003, username="three", full_name="Three"),
    ]
    photo = Photo(storage_bucket="bucket", storage_key="photos/1.jpg")
    history = ChannelHistory(message_id=10, file_id="file-1", photo=photo)
    db_session.add_all([*users, photo, history])
    await db_session.flush()
    db_session.add_all(
        [
            PhotoIdentificationVote(channel_history_id=history.id, user_id=users[0].id, animal_type="кот"),
            PhotoIdentificationVote(channel_history_id=history.id, user_id=users[1].id, animal_type="кот"),
            PhotoIdentificationVote(channel_history_id=history.id, user_id=users[2].id, animal_type="птица"),
        ]
    )
    await db_session.commit()

    queued = await identification.queue_identification_item_if_ready(db_session, history.id)

    assert queued is True
    assert history.review_status == identification.REVIEW_QUEUED
    assert history.suggested_animal_type == "кот"


@pytest.mark.asyncio
async def test_queue_identification_item_waits_without_consensus(db_session, monkeypatch):
    monkeypatch.setattr(identification.config, "IDENTIFICATION_VOTES_REQUIRED", 2)
    monkeypatch.setattr(identification.config, "IDENTIFICATION_CONSENSUS_PERCENT", 80)
    monkeypatch.setattr(identification.config, "IDENTIFICATION_MAX_VOTES_PER_PHOTO", 5)

    users = [
        User(telegram_id=1001, username="one", full_name="One"),
        User(telegram_id=1002, username="two", full_name="Two"),
        User(telegram_id=1003, username="three", full_name="Three"),
    ]
    photo = Photo(storage_bucket="bucket", storage_key="photos/1.jpg")
    history = ChannelHistory(message_id=10, file_id="file-1", photo=photo)
    db_session.add_all([*users, photo, history])
    await db_session.flush()
    db_session.add_all(
        [
            PhotoIdentificationVote(channel_history_id=history.id, user_id=users[0].id, animal_type="кот"),
            PhotoIdentificationVote(channel_history_id=history.id, user_id=users[1].id, animal_type="кот"),
            PhotoIdentificationVote(channel_history_id=history.id, user_id=users[2].id, animal_type="птица"),
        ]
    )
    await db_session.commit()

    queued = await identification.queue_identification_item_if_ready(db_session, history.id)

    assert queued is False
    assert history.review_status is None
    assert history.suggested_animal_type is None


@pytest.mark.asyncio
async def test_create_ready_identification_batches_can_send_single_ready_item(db_session, monkeypatch):
    monkeypatch.setattr(identification.config, "IDENTIFICATION_BATCH_SIZE", 10)

    photo = Photo(storage_bucket="bucket", storage_key="photos/1.jpg")
    history = ChannelHistory(
        message_id=10,
        file_id="file-1",
        photo=photo,
        review_status=identification.REVIEW_QUEUED,
        suggested_animal_type="кот",
    )
    db_session.add_all([photo, history])
    await db_session.commit()

    batch_ids = await identification.create_ready_identification_batches(db_session, min_size=1)

    assert len(batch_ids) == 1
    batch = await db_session.get(PhotoIdentificationBatch, batch_ids[0])
    assert batch is not None
    assert batch.animal_type == "кот"
    assert history.review_status == identification.REVIEW_SENT
    assert history.review_sent_at is not None


@pytest.mark.asyncio
async def test_finalize_identification_batch_awards_points_for_approved_votes(db_session, monkeypatch):
    monkeypatch.setattr(identification.config, "SCORE_OLD_PHOTO_CORRECT", 3)
    monkeypatch.setattr(identification.config, "SCORE_OLD_PHOTO_DAILY_CAP", 30)

    user = User(telegram_id=1001, username="one", full_name="One")
    photo = Photo(storage_bucket="bucket", storage_key="photos/1.jpg")
    history = ChannelHistory(
        message_id=10,
        file_id="file-1",
        photo=photo,
        review_status=identification.REVIEW_QUEUED,
        suggested_animal_type="кот",
    )
    db_session.add_all([user, photo, history])
    await db_session.flush()
    db_session.add(PhotoIdentificationVote(channel_history_id=history.id, user_id=user.id, animal_type="кот"))
    await db_session.commit()
    batch_ids = await identification.create_ready_identification_batches(db_session, min_size=1)

    result = await identification.finalize_identification_batch(db_session, batch_id=batch_ids[0])

    assert result is not None
    assert result.approved_count == 1
    assert result.rejected_count == 0
    assert result.awarded_points == 3
    assert user.score == 3
    assert history.animal_type == "кот"
    assert history.review_status == identification.REVIEW_APPROVED
    assert history.identified_by == user.id
    score_events = (await db_session.execute(select(ScoreEvent))).scalars().all()
    assert len(score_events) == 1
