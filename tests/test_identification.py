from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from bot.handlers import identify
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


class FakeIdentifyBot:
    def __init__(self):
        self.sent_photos = []
        self.edited_media = []

    async def send_photo(self, **kwargs):
        self.sent_photos.append(kwargs)
        return SimpleNamespace(message_id=777)

    async def edit_message_media(self, **kwargs):
        self.edited_media.append(kwargs)


class FakeAsyncSession:
    def __init__(self, batch):
        self.batch = batch
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def get(self, model, object_id):
        return self.batch if object_id == self.batch.id else None

    async def commit(self):
        self.committed = True


@pytest.mark.asyncio
async def test_send_assignment_edits_existing_photo_message(monkeypatch):
    bot = FakeIdentifyBot()
    channel_history = SimpleNamespace(id=1, file_id="file-1", photo=None)

    async def fake_animal_type_kb():
        return "animal-kb"

    monkeypatch.setattr(identify, "get_identification_animal_type_kb", fake_animal_type_kb)

    result = await identify._send_assignment(bot, 1001, channel_history, target_message_id=55)

    assert result is True
    assert bot.sent_photos == []
    assert len(bot.edited_media) == 1
    assert bot.edited_media[0]["chat_id"] == 1001
    assert bot.edited_media[0]["message_id"] == 55
    assert bot.edited_media[0]["media"].media == "file-1"
    assert bot.edited_media[0]["reply_markup"] == "animal-kb"


@pytest.mark.asyncio
async def test_identification_batch_to_admin_uses_single_view_message(monkeypatch):
    bot = FakeIdentifyBot()
    batch = SimpleNamespace(
        id=9,
        animal_type="кот",
        items=[
            SimpleNamespace(
                item_number=1,
                status=identification.ITEM_PENDING,
                channel_history=SimpleNamespace(id=11, file_id="file-1", photo=None),
            ),
            SimpleNamespace(
                item_number=2,
                status=identification.ITEM_PENDING,
                channel_history=SimpleNamespace(id=12, file_id="file-2", photo=None),
            ),
        ],
    )
    fake_session = FakeAsyncSession(batch)
    monkeypatch.setattr(identify, "async_session", lambda: fake_session)

    sent = await identify._send_identification_batch_to_admin(bot, batch)

    assert sent is True
    assert len(bot.sent_photos) == 1
    assert bot.sent_photos[0]["photo"] == "file-1"
    assert "Фото: 1/2" in bot.sent_photos[0]["caption"]
    assert bot.sent_photos[0]["reply_markup"] is not None
    assert batch.control_message_id == 777
    assert batch.sent_at is not None
    assert fake_session.committed is True


def test_identification_batch_view_caption_marks_rejected_item():
    item = SimpleNamespace(
        item_number=2,
        status=identification.ITEM_REJECTED,
        channel_history=SimpleNamespace(id=12, file_id="file-2", photo=None),
    )
    batch = SimpleNamespace(
        id=9,
        animal_type="кот",
        items=[
            SimpleNamespace(
                item_number=1,
                status=identification.ITEM_PENDING,
                channel_history=SimpleNamespace(id=11, file_id="file-1", photo=None),
            ),
            item,
        ],
    )

    caption = identify._identification_batch_view_caption(batch, item)

    assert "Пачка #9: кот" in caption
    assert "Фото: 2/2" in caption
    assert "Статус: исключено" in caption


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
async def test_create_ready_identification_batches_queues_existing_votes(db_session, monkeypatch):
    monkeypatch.setattr(identification.config, "IDENTIFICATION_VOTES_REQUIRED", 1)
    monkeypatch.setattr(identification.config, "IDENTIFICATION_BATCH_SIZE", 10)

    user = User(telegram_id=1001, username="one", full_name="One")
    photo = Photo(storage_bucket="bucket", storage_key="photos/1.jpg")
    history = ChannelHistory(message_id=10, file_id="file-1", photo=photo)
    db_session.add_all([user, photo, history])
    await db_session.flush()
    db_session.add(PhotoIdentificationVote(channel_history_id=history.id, user_id=user.id, animal_type="кот"))
    await db_session.commit()

    batch_ids = await identification.create_ready_identification_batches(db_session, min_size=1)

    assert len(batch_ids) == 1
    assert history.review_status == identification.REVIEW_SENT
    assert history.suggested_animal_type == "кот"
    batch = await db_session.get(PhotoIdentificationBatch, batch_ids[0])
    assert batch is not None
    assert batch.animal_type == "кот"


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
