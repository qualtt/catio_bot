import logging
from datetime import date

from aiogram import Bot, F
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from bot.content import bot_content
from bot.keyboards.inline import (
    get_admin_animal_change_kb,
    get_admin_approval_kb,
    get_admin_custom_animal_kb,
    get_admin_menu_kb,
    get_admin_post_manage_kb,
    get_admin_rejection_reason_kb,
    get_admin_reschedule_cancel_kb,
)
from bot.services.broadcast import broadcast_message
from bot.services.captions import (
    format_schedule,
)
from bot.services.publisher import publish_post
from bot.services.scoring import award_post_approval_score
from db.crud import (
    ensure_animal_type,
    get_animal_type_name,
    get_next_auto_slot,
    now_in_app_tz,
)
from db.database import async_session
from db.models.post import PostStatus

from .actions import *
from .helpers import *
from .router import AdminState, admin_router

logger = logging.getLogger(__name__)


@admin_router.callback_query(F.data == "admin_broadcast")
async def handle_admin_broadcast_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback):
        await callback.answer(bot_content.message("not_admin"), show_alert=True)
        return
    await _start_broadcast_prompt(callback.message, state)
    await callback.answer()


@admin_router.callback_query(F.data == "admin_broadcast_cancel")
async def handle_admin_broadcast_cancel(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback):
        await callback.answer(bot_content.message("not_admin"), show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(bot_content.message("admin_broadcast_cancelled"))
    await callback.answer()


@admin_router.callback_query(F.data == "admin_broadcast_send")
async def handle_admin_broadcast_send(callback: CallbackQuery, state: FSMContext, bot: Bot):
    if not is_admin(callback):
        await callback.answer(bot_content.message("not_admin"), show_alert=True)
        return

    data = await state.get_data()
    text = (data.get("broadcast_text") or "").strip()
    if not text:
        await state.clear()
        await callback.answer(bot_content.message("admin_broadcast_empty"), show_alert=True)
        return

    await callback.answer()
    await callback.message.edit_text(bot_content.message("admin_broadcast_prompt"))

    sent_count, failed_count = await broadcast_message(bot, text)
    await state.clear()
    await bot.send_message(
        chat_id=callback.from_user.id,
        text=bot_content.message("admin_broadcast_done", sent=sent_count, failed=failed_count),
        reply_markup=get_admin_menu_kb(),
    )


@admin_router.callback_query(F.data == "admin_stats")
async def handle_admin_stats(callback: CallbackQuery):
    if not is_admin(callback):
        await callback.answer(bot_content.message("not_admin"), show_alert=True)
        return

    async with async_session() as session:
        text = await load_admin_stats(session)
    await callback.message.edit_text(text, reply_markup=get_admin_menu_kb())
    await callback.answer()


@admin_router.callback_query(F.data.startswith("admin_schedule_"))
async def handle_admin_schedule(callback: CallbackQuery):
    if not is_admin(callback):
        await callback.answer(bot_content.message("not_admin"), show_alert=True)
        return

    try:
        target_date = parse_schedule_date(callback.data.removeprefix("admin_schedule_"))
    except ValueError:
        await callback.answer(bot_content.message("admin_invalid_date"), show_alert=True)
        return

    await send_admin_schedule(callback, target_date)


@admin_router.callback_query(F.data.startswith("admin_post_"))
async def handle_admin_post_manage(callback: CallbackQuery):
    if not is_admin(callback):
        await callback.answer(bot_content.message("not_admin"), show_alert=True)
        return

    _, _, post_id_raw, return_date_raw = callback.data.split("_", 3)
    try:
        post_id = int(post_id_raw)
        return_date = date.fromisoformat(return_date_raw)
    except ValueError:
        await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
        return

    async with async_session() as session:
        post = await load_post(session, post_id)

    if not post or post.status != PostStatus.APPROVED:
        await callback.answer(bot_content.message("admin_post_not_scheduled"), show_alert=True)
        return

    await callback.message.edit_text(
        admin_post_manage_text(post),
        reply_markup=get_admin_post_manage_kb(post.id, return_date),
    )
    await callback.answer()


@admin_router.callback_query(F.data.startswith("admin_publish_"))
async def handle_admin_publish_now(callback: CallbackQuery, bot: Bot):
    if not is_admin(callback):
        await callback.answer(bot_content.message("not_admin"), show_alert=True)
        return

    _, _, post_id_raw, return_date_raw = callback.data.split("_", 3)
    try:
        post_id = int(post_id_raw)
        return_date = date.fromisoformat(return_date_raw)
    except ValueError:
        await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
        return

    async with async_session() as session:
        post = await lock_post(session, post_id)
        if not post or post.status != PostStatus.APPROVED:
            await callback.answer(bot_content.message("admin_post_not_scheduled"), show_alert=True)
            return

        try:
            await publish_post(bot, session, post, published_at=now_in_app_tz())
        except Exception:
            logger.exception("Failed to publish post %d", post_id)
            await callback.answer(bot_content.message("admin_publish_failed"), show_alert=True)
            return

    await send_admin_schedule(callback, return_date, callback_text=bot_content.message("admin_published_now"))


@admin_router.callback_query(F.data.startswith("admin_reschedule_"))
async def handle_admin_reschedule_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback):
        await callback.answer(bot_content.message("not_admin"), show_alert=True)
        return

    _, _, post_id_raw, return_date_raw = callback.data.split("_", 3)
    try:
        post_id = int(post_id_raw)
        return_date = date.fromisoformat(return_date_raw)
    except ValueError:
        await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
        return

    async with async_session() as session:
        post = await load_post(session, post_id)

    if not post or post.status != PostStatus.APPROVED:
        await callback.answer(bot_content.message("admin_post_not_scheduled"), show_alert=True)
        return

    await state.set_state(AdminState.waiting_for_reschedule_time)
    await state.update_data(post_id=post_id, return_date=return_date.isoformat())
    await callback.message.edit_text(
        bot_content.message(
            "admin_reschedule_prompt",
            post_id=post_id,
            current_schedule=format_schedule(post.schedule_time),
        ),
        reply_markup=get_admin_reschedule_cancel_kb(return_date),
    )
    await callback.answer()


@admin_router.callback_query(F.data.startswith("admin_cancel_reschedule_"))
async def handle_admin_cancel_reschedule(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback):
        await callback.answer(bot_content.message("not_admin"), show_alert=True)
        return

    try:
        return_date = date.fromisoformat(callback.data.removeprefix("admin_cancel_reschedule_"))
    except ValueError:
        return_date = now_in_app_tz().date()
    await state.clear()
    await send_admin_schedule(callback, return_date)


@admin_router.callback_query(F.data.startswith("admin_album_"))
async def handle_admin_album_navigation(callback: CallbackQuery):
    if not is_admin(callback):
        await callback.answer(bot_content.message("not_admin"), show_alert=True)
        return

    try:
        _, _, direction, post_id_raw = callback.data.split("_", 3)
        post_id = int(post_id_raw)
    except (TypeError, ValueError):
        await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
        return

    async with async_session() as session:
        post = await load_post(session, post_id)
        if not post or not post.submission_group_id:
            await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
            return

        posts = await load_submission_group_posts(session, post)
        current_index = next((index for index, item in enumerate(posts) if item.id == post.id), 0)
        offset = -1 if direction == "prev" else 1
        target_post = posts[(current_index + offset) % len(posts)]
        await edit_admin_album_view_message(callback.message, session, target_post, use_media=True)

    await callback.answer()


@admin_router.callback_query(F.data.startswith("admin_approve_"))
async def handle_admin_approve(callback: CallbackQuery, bot: Bot):
    if not is_admin(callback):
        await callback.answer(bot_content.message("not_admin"), show_alert=True)
        return

    post_id = int(callback.data.split("_")[2])

    async with async_session() as session:
        post = await load_post(session, post_id)

        if not post or post.status != PostStatus.PENDING:
            await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
            return

        if post.schedule_time is None:
            schedule_time = await get_next_auto_slot(session, animal_type=post.animal_type)
            post.schedule_time = schedule_time
        else:
            schedule_time = post.schedule_time

        post.status = PostStatus.APPROVED
        await ensure_animal_type(session, post.animal_type)
        score_award = await award_post_approval_score(session, post)
        await session.commit()

        schedule_text = format_schedule(schedule_time)
        if callback_is_album_control(callback, post):
            await refresh_admin_album_control(callback, session, post)
        else:
            await callback.message.edit_caption(
                caption=bot_content.message(
                    "approved_caption",
                    schedule=schedule_text,
                    points=score_award.points,
                )
            )

        try:
            await bot.send_message(
                chat_id=post.user.telegram_id,
                text=approved_user_notification_text(
                    post,
                    schedule=schedule_text,
                    points=score_award.points,
                ),
            )
        except TelegramAPIError:
            pass
        await callback.answer(approved_callback_text(post, points=score_award.points))


@admin_router.callback_query(F.data.startswith("admin_reject_"))
async def handle_admin_reject_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback):
        await callback.answer(bot_content.message("not_admin"), show_alert=True)
        return

    post_id = int(callback.data.split("_")[2])

    async with async_session() as session:
        post = await load_post(session, post_id)

        if not post or post.status != PostStatus.PENDING:
            await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
            return

        is_album_control = callback_is_album_control(callback, post)

    await state.set_state(AdminState.waiting_for_rejection_reason)
    await state.update_data(
        reject_post_id=post_id,
        reject_message_chat_id=callback.message.chat.id,
        reject_message_id=callback.message.message_id,
        reject_is_album_control=is_album_control,
        reject_is_album_view=bool(callback.message.photo),
    )
    prompt = bot_content.message("admin_rejection_reason_prompt", post_id=post_id)
    reply_markup = get_admin_rejection_reason_kb(post_id, has_duplicate=post.duplicate_of_photo_id is not None)
    if is_album_control and callback.message.text:
        await callback.message.edit_text(prompt, reply_markup=reply_markup)
    else:
        await callback.message.edit_caption(caption=prompt, reply_markup=reply_markup)
    await callback.answer()


@admin_router.callback_query(F.data.startswith("admin_rejectreason_none_"))
async def handle_admin_reject_without_reason(callback: CallbackQuery, state: FSMContext, bot: Bot):
    if not is_admin(callback):
        await callback.answer(bot_content.message("not_admin"), show_alert=True)
        return

    post_id = int(callback.data.rsplit("_", 1)[1])
    async with async_session() as session:
        post = await load_post(session, post_id)
        if not post or post.status != PostStatus.PENDING:
            await state.clear()
            await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
            return

        is_album_control = callback_is_album_control(callback, post)
        is_album_view = bool(callback.message.photo)
        await reject_post(session, post)
        await edit_admin_rejection_result(
            bot,
            session,
            post,
            chat_id=callback.message.chat.id,
            message_id=callback.message.message_id,
            is_album_control=is_album_control,
            is_album_view=is_album_view,
        )
        await notify_rejected_post_user(bot, post)

    await state.clear()
    await callback.answer(bot_content.message("rejected_callback"))


@admin_router.callback_query(F.data.startswith("admin_rejectreason_duplicate_"))
async def handle_admin_reject_as_duplicate(callback: CallbackQuery, state: FSMContext, bot: Bot):
    if not is_admin(callback):
        await callback.answer(bot_content.message("not_admin"), show_alert=True)
        return

    post_id = int(callback.data.rsplit("_", 1)[1])
    async with async_session() as session:
        post = await load_post(session, post_id)
        if not post or post.status != PostStatus.PENDING:
            await state.clear()
            await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
            return

        reason = duplicate_rejection_reason(post)
        if reason is None:
            await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
            return

        is_album_control = callback_is_album_control(callback, post)
        is_album_view = bool(callback.message.photo)
        await reject_post(session, post, reason=reason)
        await edit_admin_rejection_result(
            bot,
            session,
            post,
            chat_id=callback.message.chat.id,
            message_id=callback.message.message_id,
            is_album_control=is_album_control,
            is_album_view=is_album_view,
            reason=reason,
        )
        await notify_rejected_post_user(bot, post, reason=reason)

    await state.clear()
    await callback.answer(bot_content.message("rejected_callback"))


@admin_router.callback_query(F.data.startswith("admin_change_"))
async def handle_admin_change(callback: CallbackQuery):
    if not is_admin(callback):
        await callback.answer(bot_content.message("not_admin"), show_alert=True)
        return

    post_id = int(callback.data.split("_")[2])
    async with async_session() as session:
        post = await load_post(session, post_id)
        if not post or post.status != PostStatus.PENDING:
            await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
            return

    await callback.message.edit_reply_markup(reply_markup=await get_admin_animal_change_kb(post_id))
    await callback.answer()


@admin_router.callback_query(F.data.startswith("admin_customanimal_"))
async def handle_admin_custom_animal_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback):
        await callback.answer(bot_content.message("not_admin"), show_alert=True)
        return

    try:
        post_id = int(callback.data.rsplit("_", 1)[1])
    except (TypeError, ValueError):
        await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
        return

    async with async_session() as session:
        post = await load_post(session, post_id)
        if not post or post.status != PostStatus.PENDING:
            await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
            return

        is_album_control = callback_is_album_control(callback, post)

    await state.set_state(AdminState.waiting_for_custom_animal_type)
    await state.update_data(
        custom_animal_post_id=post_id,
        custom_animal_message_chat_id=callback.message.chat.id,
        custom_animal_message_id=callback.message.message_id,
        custom_animal_is_album_control=is_album_control,
        custom_animal_is_album_view=bool(callback.message.photo),
    )

    prompt = bot_content.message("admin_custom_animal_type_prompt", post_id=post_id)
    if is_album_control and callback.message.text:
        await callback.message.edit_text(prompt, reply_markup=get_admin_custom_animal_kb(post_id))
    else:
        await callback.message.edit_caption(caption=prompt, reply_markup=get_admin_custom_animal_kb(post_id))
    await callback.answer()


@admin_router.callback_query(F.data.startswith("admin_back_"))
async def handle_admin_back(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback):
        await callback.answer(bot_content.message("not_admin"), show_alert=True)
        return

    await state.clear()
    post_id = int(callback.data.split("_")[2])
    async with async_session() as session:
        post = await load_post(session, post_id)
        if not post:
            await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
            return

        if callback_is_album_control(callback, post):
            await refresh_admin_album_control(callback, session, post)
        else:
            await callback.message.edit_reply_markup(reply_markup=get_admin_approval_kb(post_id))

    await callback.answer()


@admin_router.callback_query(F.data.startswith("admin_setanimal_"))
async def handle_admin_set_animal(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback):
        await callback.answer(bot_content.message("not_admin"), show_alert=True)
        return

    _, _, post_id_raw, animal_type_id_raw = callback.data.split("_", 3)
    post_id = int(post_id_raw)
    try:
        animal_type_id = int(animal_type_id_raw)
    except ValueError:
        await callback.answer(bot_content.message("animal_type_not_found"), show_alert=True)
        return

    async with async_session() as session:
        animal_type = await get_animal_type_name(session, animal_type_id)
        if not animal_type:
            await callback.answer(bot_content.message("animal_type_not_found"), show_alert=True)
            return

        post = await load_post(session, post_id)
        if not post or post.status != PostStatus.PENDING:
            await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
            return

        post.animal_type = animal_type
        await session.commit()

        if callback_is_album_control(callback, post):
            await refresh_admin_album_control(callback, session, post)
        else:
            await callback.message.edit_caption(
                caption=admin_post_caption(post),
                reply_markup=get_admin_approval_kb(post_id),
            )

    await state.clear()
    await callback.answer(bot_content.message("animal_changed"))


