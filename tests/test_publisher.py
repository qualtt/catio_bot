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

    monkeypatch.setattr("bot.services.publisher.create_channel_history_item", fake_create_channel_history_item)

    await publish_post(bot, session, post)

    assert bot.sent_photos == [
        {
            "chat_id": "-100123",
            "photo": "telegram-file-id",
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
