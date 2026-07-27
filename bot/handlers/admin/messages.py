import logging

from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.config import config
from bot.content import bot_content
from bot.keyboards.inline import (
    get_admin_broadcast_confirm_kb,
    get_admin_reschedule_cancel_kb,
)
from bot.services.broadcast import BROADCAST_MESSAGE_LIMIT
from bot.services.captions import (
    format_schedule,
)
from db.crud import (
    animal_type_has_unsupported_latin,
    canonical_animal_type,
    ensure_animal_type,
)
from db.database import async_session
from db.models.post import PostStatus

from .actions import *
from .helpers import *
from .router import AdminState, admin_router

logger = logging.getLogger(__name__)


@admin_router.message(AdminState.waiting_for_broadcast_text)
@admin_router.message(AdminState.waiting_for_broadcast_confirm)
async def handle_admin_broadcast_text(message: Message, state: FSMContext):
    if not is_admin_user(message.from_user.id):
        await message.answer(bot_content.message("not_admin"))
        return

    text = (message.text or message.caption or "").strip()
    if not text:
        await message.answer(bot_content.message("admin_broadcast_empty"))
        return
    if len(text) > BROADCAST_MESSAGE_LIMIT:
        await message.answer(
            bot_content.message("admin_broadcast_too_long", max_length=BROADCAST_MESSAGE_LIMIT)
        )
        return

    await state.set_state(AdminState.waiting_for_broadcast_confirm)
    await state.update_data(broadcast_text=text)
    await message.answer(
        bot_content.message("admin_broadcast_preview", preview=text),
        reply_markup=get_admin_broadcast_confirm_kb(),
    )


@admin_router.message(AdminState.waiting_for_reschedule_time)
async def handle_admin_reschedule_text(message: Message, state: FSMContext):
    if message.from_user.id != config.ADMIN_ID:
        await message.answer(bot_content.message("not_admin"))
        return

    data = await state.get_data()
    post_id = int(data.get("post_id") or 0)
    return_date = parse_schedule_date(data.get("return_date"))
    new_schedule = parse_admin_datetime(message.text or "")
    if new_schedule is None:
        await message.answer(
            bot_content.message("admin_reschedule_invalid"),
            reply_markup=get_admin_reschedule_cancel_kb(return_date),
        )
        return

    async with async_session() as session:
        post = await load_post(session, post_id)
        if not post or post.status != PostStatus.APPROVED:
            await state.clear()
            await message.answer(bot_content.message("admin_post_not_scheduled"))
            return

        post.schedule_time = new_schedule
        post.is_auto_scheduled = False
        await session.commit()

    await state.clear()
    await message.answer(
        bot_content.message(
            "admin_reschedule_saved",
            post_id=post_id,
            schedule=format_schedule(new_schedule),
        ),
    )
    await send_admin_schedule(message, new_schedule.date())


@admin_router.message(AdminState.waiting_for_rejection_reason)
async def handle_admin_rejection_reason_text(message: Message, state: FSMContext, bot: Bot):
    if message.from_user.id != config.ADMIN_ID:
        await message.answer(bot_content.message("not_admin"))
        return

    raw_reason = normalize_rejection_reason(message.text)
    if raw_reason is None:
        await message.answer(bot_content.message("admin_rejection_reason_empty"))
        return

    data = await state.get_data()
    post_id = int(data.get("reject_post_id") or 0)
    chat_id = int(data.get("reject_message_chat_id") or message.chat.id)
    message_id = int(data.get("reject_message_id") or 0)
    is_album_control = bool(data.get("reject_is_album_control"))
    is_album_view = bool(data.get("reject_is_album_view"))

    async with async_session() as session:
        post = await load_post(session, post_id)
        if not post or post.status != PostStatus.PENDING:
            await state.clear()
            await message.answer(bot_content.message("post_processed_or_missing"))
            return

        reason = normalize_duplicate_rejection_reason(raw_reason, post)
        await reject_post(session, post, reason=reason)
        if message_id:
            await edit_admin_rejection_result(
                bot,
                session,
                post,
                chat_id=chat_id,
                message_id=message_id,
                is_album_control=is_album_control,
                is_album_view=is_album_view,
                reason=reason,
            )
        await notify_rejected_post_user(bot, post, reason=reason)

    await state.clear()
    await message.answer(bot_content.message("rejected_callback"))


@admin_router.message(AdminState.waiting_for_custom_animal_type)
async def handle_admin_custom_animal_text(message: Message, state: FSMContext, bot: Bot):
    if message.from_user.id != config.ADMIN_ID:
        await message.answer(bot_content.message("not_admin"))
        return

    if animal_type_has_unsupported_latin(message.text):
        await message.answer(bot_content.message("invalid_custom_animal_type_layout"))
        return

    async with async_session() as session:
        animal_type = await canonical_animal_type(session, message.text)

    if not animal_type:
        await message.answer(bot_content.message("invalid_custom_animal_type"))
        return

    if animal_type.casefold() == bot_content.other_animal_label().casefold():
        await message.answer(bot_content.message("invalid_custom_animal_type"))
        return

    max_length = bot_content.animal_type_max_length()
    if len(animal_type) > max_length:
        await message.answer(bot_content.message("custom_animal_type_too_long", max_length=max_length))
        return

    data = await state.get_data()
    post_id = int(data.get("custom_animal_post_id") or 0)
    chat_id = int(data.get("custom_animal_message_chat_id") or message.chat.id)
    message_id = int(data.get("custom_animal_message_id") or 0)
    is_album_control = bool(data.get("custom_animal_is_album_control"))
    is_album_view = bool(data.get("custom_animal_is_album_view"))

    async with async_session() as session:
        post = await load_post(session, post_id)
        if not post or post.status != PostStatus.PENDING:
            await state.clear()
            await message.answer(bot_content.message("post_processed_or_missing"))
            return

        post.animal_type = animal_type
        await ensure_animal_type(session, animal_type)
        await session.commit()

        if message_id:
            await edit_admin_animal_change_result(
                bot,
                session,
                post,
                chat_id=chat_id,
                message_id=message_id,
                is_album_control=is_album_control,
                is_album_view=is_album_view,
            )

    await state.clear()
    await message.answer(bot_content.message("animal_changed"))


