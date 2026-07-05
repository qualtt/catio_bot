import pytest
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardRemove

from bot.handlers.base import (
    PHOTO_COMMAND_PATTERN,
    POST_COMMAND_PATTERN,
    _command_id,
    answer_with_legacy_reply_keyboard_removed,
)


class DummySentMessage:
    def __init__(self):
        self.edited_reply_markups = []

    async def edit_reply_markup(self, reply_markup=None, **kwargs):
        self.edited_reply_markups.append(
            {
                "reply_markup": reply_markup,
                "kwargs": kwargs,
            }
        )


class DummyMessage:
    def __init__(self):
        self.answers = []

    async def answer(self, text, reply_markup=None, **kwargs):
        sent = DummySentMessage()
        self.answers.append(
            {
                "text": text,
                "reply_markup": reply_markup,
                "kwargs": kwargs,
                "sent": sent,
            }
        )
        return sent


@pytest.mark.asyncio
async def test_answer_with_legacy_reply_keyboard_removed_keeps_single_visible_message():
    message = DummyMessage()
    inline_markup = InlineKeyboardMarkup(inline_keyboard=[])

    sent = await answer_with_legacy_reply_keyboard_removed(
        message,
        "hello",
        reply_markup=inline_markup,
        parse_mode="HTML",
    )

    assert len(message.answers) == 1
    assert message.answers[0]["text"] == "hello"
    assert message.answers[0]["reply_markup"] == ReplyKeyboardRemove()
    assert message.answers[0]["kwargs"] == {"parse_mode": "HTML"}
    assert sent.edited_reply_markups == [{"reply_markup": inline_markup, "kwargs": {}}]


def test_view_command_ids_are_parsed_with_optional_bot_username():
    assert _command_id("/photo_1528", PHOTO_COMMAND_PATTERN) == 1528
    assert _command_id("/photo_1528@catio_bot", PHOTO_COMMAND_PATTERN) == 1528
    assert _command_id("/post_42", POST_COMMAND_PATTERN) == 42
    assert _command_id("/photo_nope", PHOTO_COMMAND_PATTERN) is None
