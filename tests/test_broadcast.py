from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramAPIError

from bot.services import broadcast as broadcast_service
from db.models.user import User


class SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, traceback):
        return False


@pytest.mark.asyncio
async def test_broadcast_message_counts_success_and_failure(db_session, monkeypatch):
    monkeypatch.setattr(broadcast_service, "BROADCAST_SEND_DELAY_SECONDS", 0)
    monkeypatch.setattr(broadcast_service, "async_session", lambda: SessionContext(db_session))

    users = [
        User(telegram_id=101, username="u1", full_name="User 1"),
        User(telegram_id=102, username="u2", full_name="User 2"),
        User(telegram_id=103, username="u3", full_name="User 3"),
    ]
    db_session.add_all(users)
    await db_session.commit()

    bot = AsyncMock()
    bot.send_message = AsyncMock(
        side_effect=[None, TelegramAPIError(method="sendMessage", message="blocked"), None]
    )

    sent_count, failed_count = await broadcast_service.broadcast_message(bot, "Обновление турнира")

    assert sent_count == 2
    assert failed_count == 1
    assert bot.send_message.await_count == 3
