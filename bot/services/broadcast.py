from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from sqlalchemy import select

from db.database import async_session
from db.models.user import User


logger = logging.getLogger(__name__)

BROADCAST_MESSAGE_LIMIT = 4096
BROADCAST_SEND_DELAY_SECONDS = 0.05


async def broadcast_message(bot: Bot, text: str) -> tuple[int, int]:
    async with async_session() as session:
        users = list((await session.execute(select(User).order_by(User.id.asc()))).scalars())

    sent_count = 0
    failed_count = 0
    for user in users:
        try:
            await bot.send_message(chat_id=user.telegram_id, text=text)
            sent_count += 1
        except TelegramAPIError as error:
            failed_count += 1
            logger.warning("Broadcast failed for user %s: %s", user.id, error)
        if BROADCAST_SEND_DELAY_SECONDS:
            await asyncio.sleep(BROADCAST_SEND_DELAY_SECONDS)

    return sent_count, failed_count
