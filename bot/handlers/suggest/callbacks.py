import logging
from datetime import date, datetime, time, timedelta

from sqlalchemy.exc import IntegrityError
from aiogram import Bot, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from bot.config import config
from bot.content import bot_content
from bot.keyboards.inline import (
    get_animal_type_kb,
    get_other_animal_type_kb,
    get_time_slots_kb,
)
from db.crud import (
    combine_slot,
    create_post,
    get_animal_type_name,
    get_free_slot_times,
    get_next_auto_slot,
    is_cat_animal_type,
    now_in_app_tz,
)
from db.database import async_session

from .actions import *
from .buffer import *
from .helpers import *
from .process import *
from .router import SuggestState, suggest_router

logger = logging.getLogger(__name__)


async def select_animal_type(callback: CallbackQuery, state: FSMContext, bot: Bot, animal_type: str):
    if _get_single_submission(callback.message.message_id) is not None:
        await _select_single_animal_type(callback, animal_type)
        return

    data = await state.get_data()
    if _is_album_submission(data):
        await _handle_album_animal_selected(callback, state, bot, animal_type)
        return

    await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)


@suggest_router.callback_query(F.data == "gemini_reject")
async def handle_gemini_reject(callback: CallbackQuery, state: FSMContext, bot: Bot):
    message_id = callback.message.message_id
    single = _get_single_submission(message_id)
    if single:
        single["gemini_rejected"] = True
        from bot.keyboards.inline import get_animal_type_kb

        from .helpers import _single_photo_prompt_text
        await _edit_callback_prompt(
            callback,
            text=_single_photo_prompt_text(single),
            reply_markup=await get_animal_type_kb()
        )
        return

    data = await state.get_data()
    if _is_album_submission(data):
        items = _album_items(data)
        index = int(data.get("album_index") or 0)
        if items:
            items[index]["gemini_rejected"] = True
            await state.update_data(album_items=items)
            from .actions import _send_album_item_prompt
            await _send_album_item_prompt(bot, callback.message.chat.id, state, edit=True)
            await callback.answer()
            return
            
    await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)


@suggest_router.callback_query(F.data == "gemini_confirm")
async def handle_gemini_confirm(callback: CallbackQuery, state: FSMContext, bot: Bot):
    message_id = callback.message.message_id
    single = _get_single_submission(message_id)
    
    if single:
        gemini_data = single.get("gemini")
        if gemini_data:
            if gemini_data.get("is_valid"):
                await select_animal_type(callback, state, bot, gemini_data.get("animal"))
            else:
                _finish_single_submission(message_id)
                await callback.message.delete()
                await callback.answer("Фото отклонено.")
        return

    data = await state.get_data()
    if _is_album_submission(data):
        items = _album_items(data)
        index = int(data.get("album_index") or 0)
        if items:
            gemini_data = items[index].get("gemini")
            if not gemini_data:
                await callback.answer()
                return
            if gemini_data.get("is_valid"):
                await select_animal_type(callback, state, bot, gemini_data.get("animal"))
            else:
                del items[index]
                if not items:
                    await callback.message.delete()
                    await state.clear()
                    await callback.answer("Все фото отклонены.")
                    return
                # Adjust index if necessary
                if index >= len(items):
                    index = len(items) - 1
                await state.update_data(album_items=items, album_index=index)
                from .actions import _send_album_item_prompt
                await _send_album_item_prompt(bot, callback.message.chat.id, state, edit=True)
                await callback.answer("Фото исключено из альбома.")
        return

    await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)


@suggest_router.callback_query(F.data == "animal_other")
async def handle_other_animal_type(callback: CallbackQuery, state: FSMContext):
    single = _get_single_submission(callback.message.message_id)
    if single is not None:
        single["stage"] = "animal_other"
        await _edit_callback_prompt(
            callback,
            bot_content.message("choose_other_animal_type"),
            reply_markup=await get_other_animal_type_kb(with_album_nav=False),
        )
        await callback.answer()
        return

    data = await state.get_data()
    if not _is_album_submission(data):
        await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
        return

    await _edit_callback_prompt(
        callback,
        bot_content.message("choose_other_animal_type"),
        reply_markup=await get_other_animal_type_kb(with_album_nav=True),
    )
    await callback.answer()


@suggest_router.callback_query(F.data == "animal_back")
async def handle_animal_type_back(callback: CallbackQuery, state: FSMContext):
    single = _get_single_submission(callback.message.message_id)
    if single is not None:
        single["stage"] = "animal"
        await _edit_callback_prompt(
            callback,
            _single_photo_prompt_text(single),
            reply_markup=await get_animal_type_kb(with_album_nav=False),
        )
        await callback.answer()
        return

    data = await state.get_data()
    if not _is_album_submission(data):
        await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
        return

    await _edit_callback_prompt(
        callback,
        _album_prompt_text(data),
        reply_markup=await get_animal_type_kb(with_album_nav=True),
    )
    await callback.answer()


@suggest_router.callback_query(F.data == "animal_custom")
async def handle_custom_animal_type_button(callback: CallbackQuery, state: FSMContext):
    single = _get_single_submission(callback.message.message_id)
    if single is not None:
        single["stage"] = "custom_animal"
        _custom_animal_prompt_by_user[callback.from_user.id] = callback.message.message_id
        await _edit_callback_prompt(callback, bot_content.message("ask_custom_animal_type"))
        await callback.answer()
        return

    data = await state.get_data()
    if not _is_album_submission(data):
        await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
        return

    await state.set_state(SuggestState.waiting_for_custom_animal_type)
    await _edit_callback_prompt(callback, bot_content.message("ask_custom_animal_type"))
    await callback.answer()


@suggest_router.callback_query(SuggestState.waiting_for_animal_type, F.data.in_({"album_prev", "album_next"}))
async def handle_album_animal_navigation(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    if not _is_album_submission(data):
        await callback.answer()
        return

    items = _album_items(data)
    if not items:
        await callback.answer()
        return

    current_index = int(data.get("album_index") or 0)
    offset = -1 if callback.data == "album_prev" else 1
    next_index = (current_index + offset) % len(items)
    await state.update_data(album_index=next_index)
    await _send_album_item_prompt(bot, callback.message.chat.id, state)
    await callback.answer()


@suggest_router.callback_query(F.data.startswith("animal_id_"))
async def handle_animal_type(callback: CallbackQuery, state: FSMContext, bot: Bot):
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
    await select_animal_type(callback, state, bot, animal_type)


@suggest_router.callback_query(F.data.startswith("animal_extra_id_"))
async def handle_extra_animal_type(callback: CallbackQuery, state: FSMContext, bot: Bot):
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
    await select_animal_type(callback, state, bot, animal_type)


@suggest_router.callback_query(F.data == "schedule_auto")
async def handle_schedule_auto(callback: CallbackQuery, state: FSMContext, bot: Bot):
    single = _get_single_submission(callback.message.message_id)
    author = user_display(callback.from_user)

    if single is not None:
        file_id = single.get("file_id")
        photo_id = single.get("photo_id")
        duplicate_of_photo_id = single.get("duplicate_of_photo_id")
        duplicate_distance = single.get("duplicate_distance")
        animal_type = single.get("animal_type")
        user_id = single.get("user_id")

        try:
            async with async_session() as session:
                schedule_time = await get_next_auto_slot(session, animal_type=animal_type)
                post = await create_post(
                    session,
                    user_id=user_id,
                    file_id=file_id,
                    animal_type=animal_type,
                    is_auto_scheduled=True,
                    manual_time=schedule_time,
                    photo_id=photo_id,
                    duplicate_of_photo_id=duplicate_of_photo_id,
                    duplicate_distance=duplicate_distance,
                )
                await session.refresh(post, ["duplicate_of_photo"])
        except IntegrityError:
            _finish_single_submission(callback.message.message_id)
            await _edit_message_text_or_caption(
                callback.message, 
                "⚠️ Время сессии истекло, и фотография была удалена для экономии места. Пожалуйста, отправьте фото заново."
            )
            return

        _finish_single_submission(callback.message.message_id)
        success_text = bot_content.message(
            "photo_submitted_auto",
            schedule=_format_schedule(schedule_time),
        )
        ai_comment = (single.get("gemini") or {}).get("comment")
        if ai_comment:
            success_text += f"\n\n💬 Комментарий ИИ: {ai_comment}"
        await _edit_message_text_or_caption(callback.message, success_text)
        await _send_single_submission_to_admin(
            bot,
            post=post,
            file_id=file_id,
            animal_type=animal_type,
            schedule_time=schedule_time,
            author=author,
            ai_comment=(single.get("gemini") or {}).get("comment"),
        )
        await callback.answer()
        return

    data = await state.get_data()
    if not _is_album_submission(data):
        await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
        return

    if _is_album_submission(data):
        items = _album_items(data)
        try:
            async with async_session() as session:
                schedule_times = await _allocate_album_schedule_slots(session, items)
                posts = await _create_album_posts(
                    session,
                    data=data,
                    schedule_times=schedule_times,
                    is_auto_scheduled=True,
                )
        except IntegrityError:
            await state.clear()
            await _edit_message_text_or_caption(
                callback.message,
                "⚠️ Время сессии истекло, и некоторые фотографии были удалены для экономии места. Пожалуйста, начните заново."
            )
            return

        await state.clear()
        await _edit_message_text_or_caption(
            callback.message,
            bot_content.message(
                "album_submitted_auto",
                schedules=_album_schedule_summary(posts),
            )
        )
        await _send_album_submission_to_admin(bot, posts=posts, author=author)
        await callback.answer()
        return


@suggest_router.callback_query(F.data == "schedule_manual")
async def handle_schedule_manual(callback: CallbackQuery, state: FSMContext):
    today = now_in_app_tz().date()
    min_date = today + timedelta(days=1)

    if _get_single_submission(callback.message.message_id) is not None:
        await _edit_message_text_or_caption(
            callback.message,
            bot_content.message("choose_publication_date"),
            reply_markup=await _build_calendar_markup({}, year=min_date.year, month=min_date.month),
        )
        await callback.answer()
        return

    data = await state.get_data()
    if not _is_album_submission(data):
        await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
        return

    if _is_album_submission(data):
        items, schedule_times, schedule_auto_flags, schedule_index = _album_schedule_context(data)
        if not items:
            await callback.answer()
            return

        if schedule_times[schedule_index] is not None:
            schedule_index = _next_unscheduled_index(schedule_times, schedule_index) or schedule_index

        state_data = _album_schedule_state(schedule_times, schedule_auto_flags, schedule_index)
        await state.update_data(**state_data)
        await _show_album_schedule_calendar(callback.message, {**data, **state_data})
        await callback.answer()
        return


@suggest_router.callback_query(F.data.startswith("cal_nav_"))
async def handle_calendar_nav(callback: CallbackQuery, state: FSMContext):
    _, _, year_raw, month_raw = callback.data.split("_")
    year = int(year_raw)
    month = int(month_raw)
    today = now_in_app_tz().date()
    min_date = today + timedelta(days=1)
    max_date = min_date + timedelta(days=config.AUTO_POST_DAYS_AHEAD - 1)
    shown_date = date(year=year, month=month, day=1)
    if shown_date < min_date.replace(day=1) or shown_date > max_date.replace(day=1):
        await callback.answer()
        return

    single = _get_single_submission(callback.message.message_id)
    calendar_data = {} if single is not None else await state.get_data()
    if single is None and not _is_album_submission(calendar_data):
        await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
        return

    await callback.message.edit_reply_markup(
        reply_markup=await _build_calendar_markup(calendar_data, year=year, month=month)
    )
    await callback.answer()


@suggest_router.callback_query(F.data.startswith("cal_day_"))
async def handle_calendar_day(callback: CallbackQuery, state: FSMContext):
    _, _, day_raw = callback.data.split("_")
    target_date = date.fromisoformat(day_raw)

    async with async_session() as session:
        free_times = await get_free_slot_times(session, target_date)

    if not free_times:
        await callback.answer(bot_content.message("no_free_slots"), show_alert=True)
        return

    single = _get_single_submission(callback.message.message_id)
    data = {} if single is not None else await state.get_data()
    if single is None and not _is_album_submission(data):
        await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
        return

    footer_buttons = None
    message_kwargs = {"date": target_date.strftime("%Y-%m-%d")}
    if _is_album_submission(data):
        _, _, _, schedule_index = _album_schedule_context(data)
        free_times = _filter_selected_album_times(
            free_times,
            target_date,
            _album_selected_slots(data, exclude_index=schedule_index),
        )
        if not free_times:
            await callback.answer(bot_content.message("no_free_slots"), show_alert=True)
            return
        footer_buttons = _album_schedule_footer_buttons()
        message_key = "choose_publication_time_album"
        message_kwargs.update(_album_schedule_prompt_kwargs(data))
    else:
        message_key = "choose_publication_time"

    await _edit_message_text_or_caption(
        callback.message,
        bot_content.message(message_key, **message_kwargs),
        reply_markup=get_time_slots_kb(target_date, free_times, footer_buttons=footer_buttons),
    )
    await callback.answer()


@suggest_router.callback_query(F.data.startswith("time_"))
async def handle_manual_time(callback: CallbackQuery, state: FSMContext, bot: Bot):
    _, day_raw, time_raw = callback.data.split("_")
    target_date = date.fromisoformat(day_raw)
    slot_hour, slot_minute = [int(part) for part in time_raw.split(":", 1)]
    schedule_time = combine_slot(target_date, datetime.min.time().replace(hour=slot_hour, minute=slot_minute))

    single = _get_single_submission(callback.message.message_id)
    author = user_display(callback.from_user)

    async with async_session() as session:
        free_times = await get_free_slot_times(session, target_date)
        data = await state.get_data()
        if _is_album_submission(data) and single is None:
            _, _, _, schedule_index = _album_schedule_context(data)
            free_times = _filter_selected_album_times(
                free_times,
                target_date,
                _album_selected_slots(data, exclude_index=schedule_index),
            )

        if schedule_time.timetz().replace(tzinfo=None) not in free_times:
            await callback.answer(bot_content.message("slot_taken"), show_alert=True)
            return

        if _is_album_submission(data) and single is None:
            await _save_album_schedule_and_continue(
                callback,
                state,
                bot,
                schedule_time=schedule_time,
                is_auto_scheduled=False,
            )
            await callback.answer()
            return

        if single is None:
            await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
            return

        file_id = single.get("file_id")
        photo_id = single.get("photo_id")
        duplicate_of_photo_id = single.get("duplicate_of_photo_id")
        duplicate_distance = single.get("duplicate_distance")
        animal_type = single.get("animal_type")
        user_id = single.get("user_id")

        try:
            post = await create_post(
                session,
                user_id=user_id,
                file_id=file_id,
                animal_type=animal_type,
                is_auto_scheduled=False,
                manual_time=schedule_time,
                photo_id=photo_id,
                duplicate_of_photo_id=duplicate_of_photo_id,
                duplicate_distance=duplicate_distance,
            )
            await session.refresh(post, ["duplicate_of_photo"])
        except IntegrityError:
            _finish_single_submission(callback.message.message_id)
            await _edit_message_text_or_caption(
                callback.message, 
                "⚠️ Время сессии истекло, и фотография была удалена для экономии места. Пожалуйста, отправьте фото заново."
            )
            return

    _finish_single_submission(callback.message.message_id)
    success_text = bot_content.message(
        "photo_submitted_manual",
        schedule=_format_schedule(schedule_time),
    )
    ai_comment = (single.get("gemini") or {}).get("comment")
    if ai_comment:
        success_text += f"\n\n💬 Комментарий ИИ: {ai_comment}"
    await _edit_message_text_or_caption(callback.message, success_text)

    await _send_single_submission_to_admin(
        bot,
        post=post,
        file_id=file_id,
        animal_type=animal_type,
        schedule_time=schedule_time,
        author=author,
        ai_comment=(single.get("gemini") or {}).get("comment"),
    )
    await callback.answer()


@suggest_router.callback_query(SuggestState.waiting_for_schedule_type, F.data == "album_auto_current")
async def handle_album_auto_current(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    if not _is_album_submission(data):
        await callback.answer()
        return

    items, schedule_times, _, schedule_index = _album_schedule_context(data)
    selected_slots = _album_selected_slots(data, exclude_index=schedule_index)
    selected_cat_dates = _album_selected_cat_dates(items, schedule_times, exclude_indices={schedule_index})
    tomorrow = now_in_app_tz().date() + timedelta(days=1)
    async with async_session() as session:
        schedule_time = await _find_next_auto_slot(
            session,
            animal_type=items[schedule_index].get("animal_type"),
            start_at=combine_slot(tomorrow, time.min),
            selected_slots=selected_slots,
            selected_cat_dates=selected_cat_dates,
        )

    await _save_album_schedule_and_continue(
        callback,
        state,
        bot,
        schedule_time=schedule_time,
        is_auto_scheduled=True,
    )
    await callback.answer()


@suggest_router.callback_query(SuggestState.waiting_for_schedule_type, F.data == "album_auto_remaining")
async def handle_album_auto_remaining(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    if not _is_album_submission(data):
        await callback.answer()
        return

    items, schedule_times, schedule_auto_flags, schedule_index = _album_schedule_context(data)
    if not items:
        await callback.answer()
        return

    remaining_indices = list(range(schedule_index, len(items)))
    selected_slots = {
        schedule_time
        for index, schedule_time in enumerate(schedule_times)
        if schedule_time is not None and index not in remaining_indices
    }
    selected_cat_dates = _album_selected_cat_dates(items, schedule_times, exclude_indices=set(remaining_indices))
    tomorrow = now_in_app_tz().date() + timedelta(days=1)
    start_at = combine_slot(tomorrow, time.min)

    async with async_session() as session:
        for index in remaining_indices:
            animal_type = items[index].get("animal_type")
            schedule_time = await _find_next_auto_slot(
                session,
                animal_type=animal_type,
                start_at=start_at,
                selected_slots=selected_slots,
                selected_cat_dates=selected_cat_dates,
            )
            schedule_times[index] = schedule_time
            schedule_auto_flags[index] = True
            selected_slots.add(schedule_time)
            if is_cat_animal_type(animal_type):
                selected_cat_dates.add(schedule_time.date())
            start_at = schedule_time + timedelta(minutes=1)

    state_data = _album_schedule_state(schedule_times, schedule_auto_flags, schedule_index)
    await state.update_data(**state_data)
    await _finalize_album_submission(
        callback,
        state,
        bot,
        data={**data, **state_data},
        schedule_times=schedule_times,
        schedule_auto_flags=schedule_auto_flags,
    )
    await callback.answer()



__all__ = ['handle_album_animal_navigation', 'handle_album_auto_current', 'handle_album_auto_remaining', 'handle_animal_type', 'handle_animal_type_back', 'handle_calendar_day', 'handle_calendar_nav', 'handle_custom_animal_type_button', 'handle_extra_animal_type', 'handle_manual_time', 'handle_other_animal_type', 'handle_schedule_auto', 'handle_schedule_manual', 'logger', 'select_animal_type']
