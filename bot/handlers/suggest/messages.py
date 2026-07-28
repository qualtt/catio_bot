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

    await _ask_single_schedule(
        bot,
        chat_id=message.chat.id,
        message_id=prompt_message_id,
        animal_type=animal_type,
    )


@suggest_router.message(SuggestState.waiting_for_custom_animal_type)
async def handle_custom_animal_type(message: Message, state: FSMContext, bot: Bot):
    animal_type = await _normalize_custom_animal_type_text(message)
    if animal_type is None:
        return

    data = await state.get_data()
    if _is_album_submission(data):
        await _handle_album_custom_animal_type(message, state, bot, animal_type)
        return

    await state.clear()
    await message.answer(bot_content.message("post_processed_or_missing"))



__all__ = ['logger', 'handle_photo', 'handle_single_custom_animal_type', 'handle_custom_animal_type']
