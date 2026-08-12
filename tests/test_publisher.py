from types import SimpleNamespace

import pytest

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
