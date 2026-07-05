import pytest

from bot.services import identification
from db.models.channel_history import ChannelHistory
from db.models.photo import Photo
from db.models.photo_identification import PhotoIdentificationVote
from db.models.user import User


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
