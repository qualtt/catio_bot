import logging

from aiogram import Bot, F
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.content import bot_content

from .actions import *
from .buffer import *
from .helpers import *
from .process import *
from .router import SuggestState, WaitingSingleCustomAnimalFilter, suggest_router

logger = logging.getLogger(__name__)


@suggest_router.message(F.photo)
async def handle_photo(message: Message, state: FSMContext, bot: Bot):
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    if message.media_group_id:
        await _collect_album_message(message, state, bot)
        return

    await _process_single_photo_message(message, state, bot)


@suggest_router.message(F.text, WaitingSingleCustomAnimalFilter())
async def handle_single_custom_animal_type(message: Message, bot: Bot):
    prompt_message_id = _custom_animal_prompt_by_user[message.from_user.id]
    animal_type = await _normalize_custom_animal_type_text(message)
    if animal_type is None:
        return

    single = _get_single_submission(prompt_message_id)
    if single:
        single["animal_type"] = animal_type
        # Re-render dashboard
        from bot.keyboards.inline import get_photo_dashboard_kb

        can_submit = bool(
            single.get("animal_type") and (single.get("schedule_time") or single.get("is_auto_scheduled"))
        )
        await bot.edit_message_caption(
            chat_id=message.chat.id,
            message_id=prompt_message_id,
            caption=_photo_dashboard_text(single, is_album=False),
            reply_markup=get_photo_dashboard_kb(is_album=False, can_submit=can_submit),
        )


@suggest_router.message(SuggestState.waiting_for_custom_animal_type)
async def handle_custom_animal_type(message: Message, state: FSMContext, bot: Bot):
    animal_type = await _normalize_custom_animal_type_text(message)
    if animal_type is None:
        return

    data = await state.get_data()
    if _is_album_submission(data):
        items = _album_items(data)
        index = int(data.get("album_index") or 0)
        items[index]["animal_type"] = animal_type
        await state.update_data(album_items=items)

        # Re-render dashboard
        from bot.keyboards.inline import get_photo_dashboard_kb

        can_submit = all(
            it.get("animal_type") and (it.get("schedule_time") or it.get("is_auto_scheduled")) for it in items
        )

        prompt_chat_id = data.get("album_prompt_chat_id")
        prompt_message_id = data.get("album_prompt_message_id")

        if prompt_chat_id and prompt_message_id:
            await bot.edit_message_caption(
                chat_id=prompt_chat_id,
                message_id=prompt_message_id,
                caption=_photo_dashboard_text(data, is_album=True),
                reply_markup=get_photo_dashboard_kb(is_album=True, can_submit=can_submit, album_length=len(items)),
            )
        return

    await state.clear()
    await message.answer(bot_content.message("post_processed_or_missing"))


__all__ = [
    "handle_custom_animal_type",
    "handle_photo",
    "handle_single_custom_animal_type",
    "logger",
]
