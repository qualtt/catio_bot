import asyncio
import logging
from uuid import uuid4

from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.content import bot_content
from bot.keyboards.inline import (
    get_animal_type_kb,
)

from .actions import *
from .buffer import *
from .helpers import *
from .router import (
    ALBUM_COLLECTION_DELAY_SECONDS,
    SuggestState,
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
    except Exception:
        logger.exception("Failed to store submitted photo")
        await message.answer(bot_content.message("photo_storage_failed"))
        return

    submission_data = {
        "file_id": file_id,
        "photo_id": item["photo_id"],
        "user_id": user.id,
        "duplicate_of_photo_id": item.get("duplicate_of_photo_id"),
        "duplicate_distance": item.get("duplicate_distance"),
        "stage": "animal",
    }
    sent = await message.reply(
        _single_photo_prompt_text(submission_data),
        reply_markup=await get_animal_type_kb(),
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
        for message in messages:
            photo_size = message.photo[-1]
            items.append(
                await _store_submitted_photo(
                    bot,
                    file_id=photo_size.file_id,
                    file_unique_id=photo_size.file_unique_id,
                )
            )
    except Exception:
        logger.exception("Failed to store submitted album")
        await messages[0].answer(bot_content.message("photo_storage_failed"))
        return

    _annotate_album_internal_duplicates(items)
    await state.clear()
    await state.update_data(
        is_album=True,
        album_items=items,
        album_index=0,
        user_id=user.id,
        submission_group_id=f"album-{messages[0].chat.id}-{messages[0].media_group_id}-{uuid4().hex[:8]}",
    )
    await state.set_state(SuggestState.waiting_for_animal_type)
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


