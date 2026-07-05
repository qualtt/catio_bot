import pytest
from aiogram.types import ReplyKeyboardRemove

from bot.content import bot_content
from bot.handlers.base import remove_legacy_reply_keyboard


class DummyMessage:
    def __init__(self):
        self.answers = []

    async def answer(self, text, reply_markup=None, **kwargs):
        self.answers.append(
            {
                "text": text,
                "reply_markup": reply_markup,
                "kwargs": kwargs,
            }
        )


@pytest.mark.asyncio
async def test_remove_legacy_reply_keyboard_sends_required_text():
    message = DummyMessage()

    await remove_legacy_reply_keyboard(message)

    assert message.answers == [
        {
            "text": bot_content.message("reply_keyboard_removed"),
            "reply_markup": ReplyKeyboardRemove(),
            "kwargs": {},
        }
    ]
