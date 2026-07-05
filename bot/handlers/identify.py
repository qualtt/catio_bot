import contextlib
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, InputMediaPhoto, Message

from bot.config import config
from bot.content import bot_content
from bot.keyboards.inline import (
    get_identification_animal_type_kb,
    get_identification_batch_kb,
    get_identification_continue_kb,
    get_identification_other_animal_type_kb,
)
from bot.services.identification import (
    create_ready_identification_batches,
    finalize_identification_batch,
    get_unsent_identification_batches,
    mark_identification_batch_sent,
    submit_identification_vote,
    toggle_identification_batch_item,
)
from bot.services.photo_storage import download_photo
from db.crud import canonical_animal_type, get_animal_type_name, get_or_create_user
from db.database import async_session
from db.models.channel_history import ChannelHistory


identify_router = Router()
logger = logging.getLogger(__name__)


class IdentifyState(StatesGroup):
    waiting_for_custom_animal_type = State()


def _is_admin(user_id: int) -> bool:
    return user_id == config.ADMIN_ID


async def _photo_input_from_history_item(channel_history: ChannelHistory):
    if channel_history.photo:
        try:
            photo_bytes = await download_photo(
                storage_bucket=channel_history.photo.storage_bucket,
                storage_key=channel_history.photo.storage_key,
            )
            filename = f"{channel_history.photo.sha256 or channel_history.id}.jpg"
            return BufferedInputFile(photo_bytes, filename=filename)
        except Exception:
            logger.exception("Failed to download old photo %s from storage", channel_history.id)

    return channel_history.file_id


async def _send_assignment(bot: Bot, chat_id: int, channel_history: ChannelHistory) -> None:
    await bot.send_photo(
        chat_id=chat_id,
        photo=await _photo_input_from_history_item(channel_history),
        caption=bot_content.message("identify_photo_caption"),
        reply_markup=await get_identification_animal_type_kb(),
    )


async def _send_next_identification_photo(
    *,
    bot: Bot,
    chat_id: int,
    telegram_user,
) -> bool:
    async with async_session() as session:
        user = await get_or_create_user(
            session,
            telegram_id=telegram_user.id,
            username=telegram_user.username,
            full_name=telegram_user.full_name,
        )
        from bot.services.identification import assign_next_identification_item

        assignment = await assign_next_identification_item(session, user.id)

    if assignment is None:
        await bot.send_message(
            chat_id=chat_id,
            text=bot_content.message("identify_no_photos"),
        )
        return False

    await _send_assignment(bot, chat_id, assignment.channel_history)
    return True


@identify_router.message(Command("identify"))
async def identify_command(message: Message, state: FSMContext, bot: Bot):
    await state.clear()
    await _send_next_identification_photo(
        bot=bot,
        chat_id=message.chat.id,
        telegram_user=message.from_user,
    )


@identify_router.callback_query(F.data == "identify_next")
async def handle_identify_next(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await state.clear()
    await _send_next_identification_photo(
        bot=bot,
        chat_id=callback.message.chat.id,
        telegram_user=callback.from_user,
    )
    await callback.answer()


@identify_router.callback_query(F.data == "identify_other")
async def handle_identify_other(callback: CallbackQuery):
    await callback.message.edit_caption(
        caption=bot_content.message("choose_other_animal_type"),
        reply_markup=await get_identification_other_animal_type_kb(),
    )
    await callback.answer()


@identify_router.callback_query(F.data == "identify_back")
async def handle_identify_back(callback: CallbackQuery):
    await callback.message.edit_caption(
        caption=bot_content.message("identify_photo_caption"),
        reply_markup=await get_identification_animal_type_kb(),
    )
    await callback.answer()


@identify_router.callback_query(F.data == "identify_custom")
async def handle_identify_custom(callback: CallbackQuery, state: FSMContext):
    await state.set_state(IdentifyState.waiting_for_custom_animal_type)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(bot_content.message("ask_custom_animal_type"))
    await callback.answer()


async def _submit_identification_answer(
    *,
    bot: Bot,
    chat_id: int,
    telegram_user,
    animal_type: str,
    state: FSMContext,
    source_message: Message | None = None,
) -> None:
    async with async_session() as session:
        user = await get_or_create_user(
            session,
            telegram_id=telegram_user.id,
            username=telegram_user.username,
            full_name=telegram_user.full_name,
        )
        result = await submit_identification_vote(
            session,
            user_id=user.id,
            animal_type=animal_type,
        )

    await state.clear()
    if result.vote is None:
        await bot.send_message(
            chat_id=chat_id,
            text=bot_content.message("identify_assignment_expired"),
            reply_markup=get_identification_continue_kb(),
        )
        return

    if source_message:
        with contextlib.suppress(TelegramAPIError):
            await source_message.edit_reply_markup(reply_markup=None)

    message_key = "identify_thanks" if result.created else "identify_already_answered"
    await bot.send_message(
        chat_id=chat_id,
        text=bot_content.message(message_key, animal_type=animal_type),
        reply_markup=get_identification_continue_kb(),
    )

    if result.queued_for_review:
        await create_and_send_ready_identification_batches(bot, min_size=1)


@identify_router.callback_query(F.data.startswith("identify_animal_id_"))
async def handle_identify_animal_type(callback: CallbackQuery, state: FSMContext, bot: Bot):
    try:
        animal_type_id = int(callback.data.rsplit("_", 1)[1])
    except (TypeError, ValueError):
        await callback.answer(bot_content.message("animal_type_not_found"), show_alert=True)
        return

    async with async_session() as session:
        animal_type = await get_animal_type_name(session, animal_type_id)
    if not animal_type:
        await callback.answer(bot_content.message("animal_type_not_found"), show_alert=True)
        return

    await _submit_identification_answer(
        bot=bot,
        chat_id=callback.message.chat.id,
        telegram_user=callback.from_user,
        animal_type=animal_type,
        state=state,
        source_message=callback.message,
    )
    await callback.answer()


@identify_router.callback_query(F.data.startswith("identify_animal_extra_id_"))
async def handle_identify_extra_animal_type(callback: CallbackQuery, state: FSMContext, bot: Bot):
    try:
        animal_type_id = int(callback.data.rsplit("_", 1)[1])
    except (TypeError, ValueError):
        await callback.answer(bot_content.message("animal_type_not_found"), show_alert=True)
        return

    async with async_session() as session:
        animal_type = await get_animal_type_name(session, animal_type_id)
    if not animal_type:
        await callback.answer(bot_content.message("animal_type_not_found"), show_alert=True)
        return

    await _submit_identification_answer(
        bot=bot,
        chat_id=callback.message.chat.id,
        telegram_user=callback.from_user,
        animal_type=animal_type,
        state=state,
        source_message=callback.message,
    )
    await callback.answer()


@identify_router.message(IdentifyState.waiting_for_custom_animal_type)
async def handle_identify_custom_text(message: Message, state: FSMContext, bot: Bot):
    max_length = bot_content.animal_type_max_length()
    async with async_session() as session:
        animal_type = await canonical_animal_type(session, message.text)

    if not animal_type:
        await message.answer(bot_content.message("invalid_custom_animal_type"))
        return

    if animal_type.casefold() == bot_content.other_animal_label().casefold():
        await message.answer(bot_content.message("invalid_custom_animal_type"))
        return

    if len(animal_type) > max_length:
        await message.answer(bot_content.message("custom_animal_type_too_long", max_length=max_length))
        return

    await _submit_identification_answer(
        bot=bot,
        chat_id=message.chat.id,
        telegram_user=message.from_user,
        animal_type=animal_type,
        state=state,
    )


async def _send_identification_batch_to_admin(bot: Bot, batch) -> bool:
    media = []
    for item in batch.items:
        photo = await _photo_input_from_history_item(item.channel_history)
        media.append(
            InputMediaPhoto(
                media=photo,
                caption=bot_content.message(
                    "identification_batch_photo_caption",
                    number=item.item_number,
                    animal_type=batch.animal_type,
                ),
            )
        )

    try:
        if len(media) == 1:
            await bot.send_photo(
                chat_id=config.ADMIN_ID,
                photo=media[0].media,
                caption=media[0].caption,
            )
        else:
            await bot.send_media_group(chat_id=config.ADMIN_ID, media=media)

        control_message = await bot.send_message(
            chat_id=config.ADMIN_ID,
            text=bot_content.message(
                "identification_batch_admin_control",
                batch_id=batch.id,
                animal_type=batch.animal_type,
                count=len(batch.items),
            ),
            reply_markup=get_identification_batch_kb(batch),
        )
    except TelegramAPIError:
        logger.exception("Failed to send identification batch %s to admin", batch.id)
        return False

    async with async_session() as session:
        await mark_identification_batch_sent(
            session,
            batch_id=batch.id,
            control_message_id=control_message.message_id,
        )
    return True


async def send_unsent_identification_batches(bot: Bot) -> int:
    async with async_session() as session:
        batches = await get_unsent_identification_batches(session)

    sent_count = 0
    for batch in batches:
        if await _send_identification_batch_to_admin(bot, batch):
            sent_count += 1
    return sent_count


async def create_and_send_ready_identification_batches(
    bot: Bot,
    *,
    min_size: int | None = None,
) -> int:
    async with async_session() as session:
        await create_ready_identification_batches(session, min_size=min_size)
    return await send_unsent_identification_batches(bot)


@identify_router.message(Command("review_old"))
async def review_old_command(message: Message, bot: Bot):
    if not _is_admin(message.from_user.id):
        await message.answer(bot_content.message("not_admin"))
        return

    sent_count = await create_and_send_ready_identification_batches(bot, min_size=1)
    await message.answer(bot_content.message("identification_review_sent", count=sent_count))


@identify_router.callback_query(F.data.startswith("ident_item_"))
async def handle_identification_batch_item(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer(bot_content.message("not_admin"), show_alert=True)
        return

    try:
        _, _, batch_id_raw, item_number_raw = callback.data.split("_", 3)
        batch_id = int(batch_id_raw)
        item_number = int(item_number_raw)
    except (TypeError, ValueError):
        await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
        return

    async with async_session() as session:
        batch = await toggle_identification_batch_item(
            session,
            batch_id=batch_id,
            item_number=item_number,
        )

    if batch is None:
        await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
        return

    await callback.message.edit_reply_markup(reply_markup=get_identification_batch_kb(batch))
    await callback.answer()


@identify_router.callback_query(F.data.startswith("ident_batch_done_"))
async def handle_identification_batch_done(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer(bot_content.message("not_admin"), show_alert=True)
        return

    batch_id = int(callback.data.rsplit("_", 1)[1])
    async with async_session() as session:
        result = await finalize_identification_batch(session, batch_id=batch_id)

    if result is None:
        await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
        return

    await callback.message.edit_text(
        bot_content.message(
            "identification_batch_completed",
            approved=result.approved_count,
            rejected=result.rejected_count,
            points=result.awarded_points,
        )
    )
    await callback.answer()


@identify_router.callback_query(F.data.startswith("ident_batch_reject_"))
async def handle_identification_batch_reject(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer(bot_content.message("not_admin"), show_alert=True)
        return

    batch_id = int(callback.data.rsplit("_", 1)[1])
    async with async_session() as session:
        result = await finalize_identification_batch(session, batch_id=batch_id, reject_all=True)

    if result is None:
        await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
        return

    await callback.message.edit_text(
        bot_content.message(
            "identification_batch_completed",
            approved=result.approved_count,
            rejected=result.rejected_count,
            points=result.awarded_points,
        )
    )
    await callback.answer()
