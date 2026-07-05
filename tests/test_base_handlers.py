from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardRemove

from bot.handlers import base
from bot.handlers.base import (
    PHOTO_COMMAND_PATTERN,
    POST_COMMAND_PATTERN,
    _command_id,
    _send_stored_photo,
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


class FallbackPhotoBot:
    def __init__(self):
        self.sent_photos = []

    async def send_photo(self, **kwargs):
        self.sent_photos.append(kwargs)
        if len(self.sent_photos) == 1:
            raise TelegramBadRequest(
                method=SimpleNamespace(),
                message="Bad Request: wrong file identifier/HTTP URL specified",
            )


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


@pytest.mark.asyncio
async def test_send_stored_photo_falls_back_to_storage_for_invalid_file_id(monkeypatch):
    bot = FallbackPhotoBot()
    photo = SimpleNamespace(
        id=1417,
        telegram_file_id="bad-file-id",
        storage_bucket="bucket",
        storage_key="photos/1417.jpg",
        sha256="abc123",
    )

    async def fake_download_photo(**kwargs):
        assert kwargs == {"storage_bucket": "bucket", "storage_key": "photos/1417.jpg"}
        return b"image-bytes"

    monkeypatch.setattr(base, "download_photo", fake_download_photo)

    await _send_stored_photo(bot, chat_id=1001, photo=photo, caption="Фото /photo_1417")

    assert len(bot.sent_photos) == 2
    assert bot.sent_photos[0]["photo"] == "bad-file-id"
    assert isinstance(bot.sent_photos[1]["photo"], BufferedInputFile)
    assert bot.sent_photos[1]["photo"].data == b"image-bytes"
