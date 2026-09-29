from datetime import timedelta
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramServerError

from bot.services.publisher import publish_post
from db.models.post import Post, PostStatus


class FakeBot:
    def __init__(self):
        self.sent_photos = []
        self.sent_messages = []

    async def send_photo(self, **kwargs):
        self.sent_photos.append(kwargs)
        return SimpleNamespace(message_id=123)

    async def send_message(self, **kwargs):
        self.sent_messages.append(kwargs)


class FakeSession:
    def __init__(self):
        self.committed = False
        self.rolled_back = False

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True


@pytest.mark.asyncio
async def test_publish_post_sends_photo_without_caption(monkeypatch):
    bot = FakeBot()
    session = FakeSession()
    post = Post(id=1, user_id=1, file_id="telegram-file-id", animal_type="кот", photo_id=44)
    indexed = {}

    async def fake_create_channel_history_item(*args, **kwargs):
        indexed.update(kwargs)

    monkeypatch.setattr(
        "bot.services.publisher.create_channel_history_item",
        fake_create_channel_history_item,
    )

    await publish_post(bot, session, post)

    assert bot.sent_photos == [
        {
            "chat_id": "-100123",
            "photo": "telegram-file-id",
            "request_timeout": 300,
        }
    ]
    assert post.status == PostStatus.PUBLISHED
    assert post.message_id == 123
    assert session.committed is True
    assert indexed["chat_id"] == -100123
    assert indexed["message_id"] == 123
    assert indexed["photo_id"] == 44
    assert indexed["file_id"] == "telegram-file-id"
    assert indexed["animal_type"] == "кот"
    assert indexed["published_at"] is not None


@pytest.mark.asyncio
async def test_publish_due_posts_defers_schedule_time_on_error(db_session, monkeypatch):
    from aiogram.exceptions import TelegramServerError

    from bot.services.publisher import publish_due_posts
    from db.crud import get_or_create_user, now_in_app_tz

    now = now_in_app_tz()
    user = await get_or_create_user(db_session, telegram_id=123, full_name="User")
    post = Post(
        user_id=user.id,
        file_id="photo123",
        animal_type="кот",
        status=PostStatus.APPROVED,
        schedule_time=now,
    )
    db_session.add(post)
    await db_session.commit()

    class ErrorBot:
        async def send_photo(self, **kwargs):
            raise TelegramServerError(method="sendPhoto", message="Gateway Timeout")

    class SessionContext:
        def __init__(self, session):
            self.session = session

        async def __aenter__(self):
            return self.session

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    monkeypatch.setattr("bot.services.publisher.async_session", lambda: SessionContext(db_session))
    monkeypatch.setattr("bot.services.publisher.now_in_app_tz", lambda: now)

    published = await publish_due_posts(ErrorBot())
    assert published == 0

    await db_session.refresh(post)
    assert post.status == PostStatus.APPROVED
    assert post.schedule_time.replace(tzinfo=None) > now.replace(tzinfo=None)


@pytest.mark.asyncio
async def test_publish_post_recovers_if_message_exists_in_channel(db_session, monkeypatch):
    from aiogram.exceptions import TelegramServerError

    from bot.config import config
    from bot.services.publisher import publish_post
    from db.crud import create_channel_history_item, get_or_create_user, now_in_app_tz

    monkeypatch.setattr(config, "CHANNEL_ID", "-100123")
    monkeypatch.setattr(config, "ADMIN_ID", 999)

    now = now_in_app_tz()
    user = await get_or_create_user(db_session, telegram_id=123, full_name="User")
    post = Post(
        user_id=user.id,
        file_id="photo123",
        animal_type="кот",
        status=PostStatus.APPROVED,
        schedule_time=now,
    )
    db_session.add(post)
    await db_session.commit()

    await create_channel_history_item(
        db_session,
        chat_id=-100123,
        message_id=500,
        photo_id=None,
        file_id="prev",
        published_at=now,
        animal_type="кот",
    )

    class RecoverBot:
        async def send_photo(self, **kwargs):
            raise TelegramServerError(method="sendPhoto", message="Gateway Timeout")

        async def forward_message(self, chat_id, from_chat_id, message_id):
            if message_id == 501:
                return SimpleNamespace(message_id=501)
            raise TelegramServerError(method="forwardMessage", message="Bad Request: message to forward not found")

    await publish_post(RecoverBot(), db_session, post)

    await db_session.refresh(post)
    assert post.status == PostStatus.PUBLISHED
    assert post.message_id == 501


class PhotoBot(FakeBot):
    def __init__(self, telegram_data: bytes | None):
        super().__init__()
        self.telegram_data = telegram_data

    async def get_file(self, file_id):
        if self.telegram_data is None:
            raise TelegramServerError(method="getFile", message="Bad Gateway")
        return SimpleNamespace(file_path="photos/file.jpg")

    async def download_file(self, file_path, destination):
        destination.write(self.telegram_data)


def _post_with_missing_s3_photo(sha256: str) -> Post:
    from db.models.photo import Photo

    photo = Photo(
        id=7,
        telegram_file_id="photo-file-id",
        storage_bucket="test-bucket",
        storage_key="catio-bot/photos/submissions/7.jpg",
        sha256=sha256,
        content_type="image/jpeg",
    )
    return Post(id=1, user_id=1, file_id="post-file-id", animal_type="кот", photo_id=7, photo=photo)


@pytest.fixture
def missing_s3_object(monkeypatch):
    uploads = []

    async def fake_download_photo(**kwargs):
        raise RuntimeError("NoSuchKey")

    class FakeS3:
        def put_object(self, **kwargs):
            uploads.append(kwargs)

    async def fake_create_channel_history_item(*args, **kwargs):
        return None

    monkeypatch.setattr("bot.services.publisher.download_photo", fake_download_photo)
    monkeypatch.setattr("bot.services.photo_storage._s3_client", lambda: FakeS3())
    monkeypatch.setattr("bot.services.publisher.create_channel_history_item", fake_create_channel_history_item)
    return uploads


@pytest.mark.asyncio
async def test_publish_post_restores_missing_s3_photo_from_telegram(missing_s3_object):
    import hashlib

    data = b"real-photo-bytes"
    bot = PhotoBot(telegram_data=data)
    post = _post_with_missing_s3_photo(hashlib.sha256(data).hexdigest())

    await publish_post(bot, FakeSession(), post)

    assert post.status == PostStatus.PUBLISHED
    assert bot.sent_photos[0]["photo"].data == data
    assert missing_s3_object == [
        {
            "Bucket": "test-bucket",
            "Key": "catio-bot/photos/submissions/7.jpg",
            "Body": data,
            "ContentType": "image/jpeg",
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("telegram_data", [None, b"different-bytes"])
async def test_publish_post_falls_back_to_file_id_when_restore_fails(missing_s3_object, telegram_data):
    bot = PhotoBot(telegram_data=telegram_data)
    post = _post_with_missing_s3_photo("expected-sha")

    await publish_post(bot, FakeSession(), post)

    assert post.status == PostStatus.PUBLISHED
    assert bot.sent_photos[0]["photo"] == "post-file-id"
    assert missing_s3_object == []


class _SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _FailingBot:
    async def send_photo(self, **kwargs):
        raise TelegramServerError(method="sendPhoto", message="Gateway Timeout")


async def _run_failing_publish(db_session, monkeypatch, *, publish_attempts: int):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from bot.config import config
    from bot.services.publisher import publish_due_posts
    from db.crud import get_or_create_user

    # 01:17 at night, as when the stuck album posts finally went out.
    now = datetime(2026, 9, 29, 1, 17, tzinfo=ZoneInfo("Europe/Moscow"))
    monkeypatch.setattr(config, "PUBLISH_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(config, "DAILY_SLOT_TIMES", "11:00")
    monkeypatch.setattr("bot.services.publisher.async_session", lambda: _SessionContext(db_session))
    monkeypatch.setattr("bot.services.publisher.now_in_app_tz", lambda: now)

    user = await get_or_create_user(db_session, telegram_id=123, full_name="User")
    post = Post(
        user_id=user.id,
        file_id="photo123",
        animal_type="кот",
        status=PostStatus.APPROVED,
        schedule_time=now,
        publish_attempts=publish_attempts,
    )
    db_session.add(post)
    await db_session.commit()

    assert await publish_due_posts(_FailingBot()) == 0
    await db_session.refresh(post)
    return post, now


@pytest.mark.asyncio
async def test_publish_due_posts_retries_soon_before_attempt_limit(db_session, monkeypatch):
    post, now = await _run_failing_publish(db_session, monkeypatch, publish_attempts=1)

    assert post.status == PostStatus.APPROVED
    assert post.publish_attempts == 2
    assert post.schedule_time.replace(tzinfo=None) == (now + timedelta(minutes=5)).replace(tzinfo=None)


@pytest.mark.asyncio
async def test_publish_due_posts_moves_post_to_regular_slot_after_attempt_limit(db_session, monkeypatch):
    post, now = await _run_failing_publish(db_session, monkeypatch, publish_attempts=2)

    assert post.status == PostStatus.APPROVED
    assert post.publish_attempts == 0
    assert post.schedule_time.replace(tzinfo=None) == now.replace(hour=11, minute=0, tzinfo=None)
