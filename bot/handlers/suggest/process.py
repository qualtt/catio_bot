import asyncio
import logging
from uuid import uuid4

from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.config import config
from bot.content import bot_content
from bot.keyboards.inline import (
    get_photo_dashboard_kb,
)
from bot.services.gemini import analyze_photo

from .actions import *
from .buffer import *
from .helpers import *
from .router import (
    ALBUM_COLLECTION_DELAY_SECONDS,
)

logger = logging.getLogger(__name__)


_album_lock = asyncio.Lock()


async def _process_single_photo_message(message: Message, state: FSMContext, bot: Bot) -> None:
    photo_size = message.photo[-1]
    file_id = photo_size.file_id
    file_unique_id = photo_size.file_unique_id

    try:
        user = await _get_or_create_submission_user(message)
        if user.is_muted:
            await message.answer(bot_content.message("user_muted"))
            return

        item = await _store_submitted_photo(bot, file_id=file_id, file_unique_id=file_unique_id)
        from aiogram.utils.chat_action import ChatActionSender

        gemini_result = None
        if config.ENABLE_GEMINI:
            async with ChatActionSender.typing(bot=bot, chat_id=message.chat.id):
                gemini_result = await analyze_photo(bot, file_id)
    except Exception:
        logger.exception("Failed to store submitted photo")
        await message.answer(bot_content.message("photo_storage_failed"))
        return

    # Auto-assign animal_type if gemini says it's valid
    animal_type = None
    if gemini_result and gemini_result.get("is_valid"):
        animal_type = gemini_result.get("animal")

    submission_data = {
        "file_id": file_id,
        "photo_id": item["photo_id"],
        "user_id": user.id,
        "duplicate_of_photo_id": item.get("duplicate_of_photo_id"),
        "duplicate_distance": item.get("duplicate_distance"),
        "gemini": gemini_result,
        "animal_type": animal_type,
    }

    # Can submit if we have both an animal type and a schedule time
    can_submit = bool(
        submission_data.get("animal_type")
        and submission_data.get("schedule_time")
        or submission_data.get("is_auto_scheduled")
    )

    sent = await message.reply(
        _photo_dashboard_text(submission_data, is_album=False),
        reply_markup=get_photo_dashboard_kb(is_album=False, can_submit=can_submit),
    )
    _set_single_submission(sent.message_id, submission_data)


async def _process_album_messages(messages: list[Message], state: FSMContext, bot: Bot) -> None:
    messages = sorted(messages, key=lambda item: item.message_id)
    if len(messages) <= 1:
        await _process_single_photo_message(messages[0], state, bot)
        return

    try:
        user = await _get_or_create_submission_user(messages[0])
        if user.is_muted:
            await messages[0].answer(bot_content.message("user_muted"))
            return

        items = []
        from aiogram.utils.chat_action import ChatActionSender

        async with ChatActionSender.typing(bot=bot, chat_id=messages[0].chat.id):
            for message in messages:
                photo_size = message.photo[-1]
                item = await _store_submitted_photo(
                    bot,
                    file_id=photo_size.file_id,
                    file_unique_id=photo_size.file_unique_id,
                )

                gemini_result = None
                if config.ENABLE_GEMINI:
                    gemini_result = await analyze_photo(bot, photo_size.file_id)

                # Auto-assign animal_type if gemini says it's valid
                animal_type = None
                if gemini_result and gemini_result.get("is_valid"):
                    animal_type = gemini_result.get("animal")

                items.append(
                    {
                        "file_id": photo_size.file_id,
                        "photo_id": item["photo_id"],
                        "duplicate_of_photo_id": item.get("duplicate_of_photo_id"),
                        "duplicate_distance": item.get("duplicate_distance"),
                        "gemini": gemini_result,
                        "animal_type": animal_type,
                    }
                )
    except Exception:
        logger.exception("Failed to store submitted album")
        await messages[0].answer(bot_content.message("photo_storage_failed"))
        return

    _annotate_album_internal_duplicates(items)
    data = {
        "is_album": True,
        "album_items": items,
        "album_index": 0,
        "user_id": user.id,
        "submission_group_id": f"album-{messages[0].chat.id}-{messages[0].media_group_id}-{uuid4().hex[:8]}",
    }
    await state.clear()
    await state.update_data(**data)
    await _send_album_item_prompt(bot, messages[0].chat.id, state, include_warning=True)


async def _flush_album_buffer_after_delay(key: tuple[int, str]) -> None:
    try:
        await asyncio.sleep(ALBUM_COLLECTION_DELAY_SECONDS)
    except asyncio.CancelledError:
        return

    async with _album_lock:
        buffer = _album_buffers.pop(key, None)

    if buffer is None:
        return

    await _process_album_messages(buffer.messages, buffer.state, buffer.bot)


async def _collect_album_message(message: Message, state: FSMContext, bot: Bot) -> None:
    key = (message.chat.id, message.media_group_id)
    async with _album_lock:
        buffer = _album_buffers.get(key)
        if buffer is None:
            buffer = AlbumBuffer(messages=[], state=state, bot=bot)
            _album_buffers[key] = buffer

        buffer.messages.append(message)
        buffer.state = state
        buffer.bot = bot

        if buffer.task and not buffer.task.done():
            buffer.task.cancel()
        buffer.task = asyncio.create_task(_flush_album_buffer_after_delay(key))


__all__ = [
    "_album_lock",
    "_collect_album_message",
    "_flush_album_buffer_after_delay",
    "_process_album_messages",
    "_process_single_photo_message",
    "logger",
]
