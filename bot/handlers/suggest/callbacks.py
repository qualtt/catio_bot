import logging
from datetime import date, datetime, timedelta

from aiogram import Bot, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.exc import IntegrityError

from bot.config import config
from bot.content import bot_content
from bot.keyboards.inline import (
    get_animal_type_kb,
    get_other_animal_type_kb,
    get_photo_dashboard_kb,
    get_time_slots_kb,
)
from db.crud import (
    combine_slot,
    create_post,
    get_animal_type_name,
    get_free_slot_times,
    get_next_auto_slot,
    now_in_app_tz,
)
from db.database import async_session

from .actions import *
from .buffer import *
from .helpers import *
from .process import *
from .router import SuggestState, suggest_router

logger = logging.getLogger(__name__)


async def _render_dashboard(callback: CallbackQuery, state: FSMContext, bot: Bot):
    single = _get_single_submission(callback.message.message_id)
    if single is not None:
        can_submit = bool(
            single.get("animal_type") and (single.get("schedule_time") or single.get("is_auto_scheduled"))
        )
        await _edit_callback_prompt(
            callback,
            _photo_dashboard_text(single, is_album=False),
            reply_markup=get_photo_dashboard_kb(is_album=False, can_submit=can_submit),
        )
        return

    data = await state.get_data()
    if _is_album_submission(data):
        items = _album_items(data)
        can_submit = all(
            it.get("animal_type") and (it.get("schedule_time") or it.get("is_auto_scheduled")) for it in items
        )
        await _edit_callback_prompt(
            callback,
            _photo_dashboard_text(data, is_album=True),
            reply_markup=get_photo_dashboard_kb(is_album=True, can_submit=can_submit, album_length=len(items)),
        )


async def select_animal_type(callback: CallbackQuery, state: FSMContext, bot: Bot, animal_type: str):
    single = _get_single_submission(callback.message.message_id)
    if single is not None:
        single["animal_type"] = animal_type
        await _render_dashboard(callback, state, bot)
        await callback.answer()
        return

    data = await state.get_data()
    if _is_album_submission(data):
        items = _album_items(data)
        index = int(data.get("album_index") or 0)
        items[index]["animal_type"] = animal_type
        await state.update_data(album_items=items)
        await _render_dashboard(callback, state, bot)
        await callback.answer()
        return

    await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)


@suggest_router.callback_query(F.data.in_({"album_prev", "album_next"}))
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
    logger.info(
        "Navigating album: current %s, next %s (offset %s)",
        current_index,
        next_index,
        offset,
    )
    await state.update_data(album_index=next_index)
    await _send_album_item_prompt(bot, callback.message.chat.id, state)
    await callback.answer()


@suggest_router.callback_query(F.data == "dash_change_type")
async def handle_dash_change_type(callback: CallbackQuery, state: FSMContext, bot: Bot):
    single = _get_single_submission(callback.message.message_id)
    data = await state.get_data()
    is_album = _is_album_submission(data)

    if single is None and not is_album:
        await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
        return

    await _edit_callback_prompt(
        callback,
        bot_content.message("ask_animal_type"),
        reply_markup=await get_animal_type_kb(with_album_nav=is_album),
    )
    await callback.answer()


@suggest_router.callback_query(F.data == "dash_set_auto")
async def handle_dash_set_auto(callback: CallbackQuery, state: FSMContext, bot: Bot):
    single = _get_single_submission(callback.message.message_id)

    if single is not None:
        animal_type = single.get("animal_type")
        if not animal_type:
            await callback.answer("Сначала выберите тип животного!", show_alert=True)
            return

        async with async_session() as session:
            schedule_time = await get_next_auto_slot(session, animal_type=animal_type)

        single["is_auto_scheduled"] = True
        single["schedule_time"] = schedule_time.isoformat() if schedule_time else None
        await _render_dashboard(callback, state, bot)
        await callback.answer()
        return

    data = await state.get_data()
    if _is_album_submission(data):
        items = _album_items(data)
        index = int(data.get("album_index") or 0)
        item = items[index]

        animal_type = item.get("animal_type")
        if not animal_type:
            await callback.answer("Сначала выберите тип животного!", show_alert=True)
            return

        # In this simplified flow, we'll just ask DB for next slot. But to avoid collisions inside album, we should filter.
        # We can reuse the album schedule allocation logic for this single item, but let's just find next free slot.
        # Actually _allocate_album_schedule_slots does exactly this for all items.

        # Let's do a simple _allocate_album_schedule_slots call for this one item.
        # Wait, if we use it, it resets others? No, let's just use it on the whole array.
        async with async_session() as session:
            # Re-allocate everything that is marked auto
            schedule_times = await _allocate_album_schedule_slots(session, items)

        item["is_auto_scheduled"] = True
        # For album, auto means it gets calculated at submission, but let's set it now for preview
        items[index]["schedule_time"] = schedule_times[index].isoformat() if schedule_times[index] else None

        # Update all auto items just in case they shifted
        for i, it in enumerate(items):
            if it.get("is_auto_scheduled"):
                it["schedule_time"] = schedule_times[i].isoformat() if schedule_times[i] else None

        await state.update_data(album_items=items)
        await _render_dashboard(callback, state, bot)
        await callback.answer()
        return


@suggest_router.callback_query(F.data == "dash_album_auto_all")
async def handle_dash_album_auto_all(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    if not _is_album_submission(data):
        await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
        return

    items = _album_items(data)

    if any(not it.get("animal_type") for it in items):
        await callback.answer("Сначала выберите тип животного для всех фото!", show_alert=True)
        return

    for item in items:
        item["is_auto_scheduled"] = True

    async with async_session() as session:
        schedule_times = await _allocate_album_schedule_slots(session, items)

    for i, item in enumerate(items):
        item["schedule_time"] = schedule_times[i].isoformat() if schedule_times[i] else None

    await state.update_data(album_items=items)
    await _render_dashboard(callback, state, bot)
    await callback.answer("Автоматическое время назначено для всех фото.")


@suggest_router.callback_query(F.data == "dash_set_manual")
async def handle_dash_set_manual(callback: CallbackQuery, state: FSMContext):
    single = _get_single_submission(callback.message.message_id)
    data = await state.get_data()

    if single is None and not _is_album_submission(data):
        await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
        return

    if single:
        if not single.get("animal_type"):
            await callback.answer("Сначала выберите тип животного!", show_alert=True)
            return
    else:
        items = _album_items(data)
        index = int(data.get("album_index") or 0)
        if not items[index].get("animal_type"):
            await callback.answer("Сначала выберите тип животного!", show_alert=True)
            return

    today = now_in_app_tz().date()
    min_date = today + timedelta(days=1)

    await _edit_message_text_or_caption(
        callback.message,
        bot_content.message("choose_publication_date"),
        reply_markup=await _build_calendar_markup({}, year=min_date.year, month=min_date.month),
    )
    await callback.answer()


@suggest_router.callback_query(F.data == "dash_submit")
async def handle_dash_submit(callback: CallbackQuery, state: FSMContext, bot: Bot):
    single = _get_single_submission(callback.message.message_id)
    if not single:
        await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
        return

    file_id = single.get("file_id")
    photo_id = single.get("photo_id")
    duplicate_of_photo_id = single.get("duplicate_of_photo_id")
    duplicate_distance = single.get("duplicate_distance")
    animal_type = single.get("animal_type")
    user_id = single.get("user_id")
    schedule_time_str = single.get("schedule_time")
    schedule_time = datetime.fromisoformat(schedule_time_str) if schedule_time_str else None
    is_auto_scheduled = single.get("is_auto_scheduled", False)
    author = user_display(callback.from_user)

    if not animal_type or not schedule_time:
        await callback.answer("Заполните все поля!", show_alert=True)
        return

    try:
        async with async_session() as session:
            # Check if slot is still free if manual
            if not is_auto_scheduled:
                free_times = await get_free_slot_times(session, schedule_time.date())
                if schedule_time.timetz().replace(tzinfo=None) not in free_times:
                    single["schedule_time"] = None
                    await callback.answer(
                        "К сожалению, это время уже занято. Пожалуйста, выберите другое.",
                        show_alert=True,
                    )
                    await _render_dashboard(callback, state, bot)
                    return

            post = await create_post(
                session,
                user_id=user_id,
                file_id=file_id,
                animal_type=animal_type,
                is_auto_scheduled=is_auto_scheduled,
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
            "⚠️ Время сессии истекло, и фотография была удалена для экономии места. Пожалуйста, отправьте фото заново.",
        )
        return

    _finish_single_submission(callback.message.message_id)
    success_text = bot_content.message(
        "photo_submitted_auto" if is_auto_scheduled else "photo_submitted_manual",
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
        ai_comment=ai_comment,
    )
    await callback.answer()


@suggest_router.callback_query(F.data == "dash_submit_album")
async def handle_dash_submit_album(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    if not _is_album_submission(data):
        await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
        return

    items = _album_items(data)
    if any(not it.get("animal_type") or not it.get("schedule_time") for it in items):
        await callback.answer("Заполните все поля для всех фотографий альбома!", show_alert=True)
        return

    author = user_display(callback.from_user)

    try:
        async with async_session() as session:
            # Check manual slots for collisions
            from .actions import _first_album_schedule_conflict

            schedule_times = [
                datetime.fromisoformat(it["schedule_time"]) if it.get("schedule_time") else None for it in items
            ]
            conflict_index = await _first_album_schedule_conflict(session, schedule_times)

            if conflict_index is not None:
                items[conflict_index]["schedule_time"] = None
                items[conflict_index]["is_auto_scheduled"] = False
                await state.update_data(album_items=items, album_index=conflict_index)
                await callback.answer(
                    f"Время для фото {conflict_index + 1} уже занято. Пожалуйста, выберите другое.",
                    show_alert=True,
                )
                await _render_dashboard(callback, state, bot)
                return

            schedule_auto_flags = [it.get("is_auto_scheduled", False) for it in items]

            posts = await _create_album_posts(
                session,
                data=data,
                schedule_times=schedule_times,
                schedule_auto_flags=schedule_auto_flags,
            )
    except IntegrityError:
        await state.clear()
        await _edit_message_text_or_caption(
            callback.message,
            "⚠️ Время сессии истекло, и некоторые фотографии были удалены для экономии места. Пожалуйста, начните заново.",
        )
        return

    await state.clear()
    # It might be mixed auto/manual, so we just use the album submitted text
    await _edit_message_text_or_caption(
        callback.message,
        bot_content.message(
            "album_submitted_auto",  # or album_submitted_manual
            schedules=_album_schedule_summary(posts),
        ),
    )
    await _send_album_submission_to_admin(bot, posts=posts, author=author)
    await callback.answer()


@suggest_router.callback_query(F.data == "animal_other")
async def handle_other_animal_type(callback: CallbackQuery, state: FSMContext):
    single = _get_single_submission(callback.message.message_id)
    data = await state.get_data()
    is_album = _is_album_submission(data)

    if single is None and not is_album:
        await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
        return

    await _edit_callback_prompt(
        callback,
        bot_content.message("choose_other_animal_type"),
        reply_markup=await get_other_animal_type_kb(with_album_nav=is_album),
    )
    await callback.answer()


@suggest_router.callback_query(F.data == "animal_back")
async def handle_animal_type_back(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await _render_dashboard(callback, state, bot)


@suggest_router.callback_query(F.data == "animal_custom")
async def handle_custom_animal_type_button(callback: CallbackQuery, state: FSMContext):
    single = _get_single_submission(callback.message.message_id)
    data = await state.get_data()
    is_album = _is_album_submission(data)

    if single is None and not is_album:
        await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
        return

    if single:
        single["stage"] = "custom_animal"
        _custom_animal_prompt_by_user[callback.from_user.id] = callback.message.message_id
    else:
        await state.set_state(SuggestState.waiting_for_custom_animal_type)

    await _edit_callback_prompt(callback, bot_content.message("ask_custom_animal_type"))
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
    message_key = "choose_publication_time"

    if _is_album_submission(data):
        # We only check slots against OTHER selected items in this album
        items = _album_items(data)
        index = int(data.get("album_index") or 0)
        selected_slots = {
            datetime.fromisoformat(it["schedule_time"])
            for i, it in enumerate(items)
            if i != index and it.get("schedule_time")
        }
        free_times = _filter_selected_album_times(
            free_times,
            target_date,
            selected_slots,
        )
        if not free_times:
            await callback.answer(bot_content.message("no_free_slots"), show_alert=True)
            return

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
    data = await state.get_data()

    if single is None and not _is_album_submission(data):
        await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
        return

    async with async_session() as session:
        free_times = await get_free_slot_times(session, target_date)

        if _is_album_submission(data) and single is None:
            items = _album_items(data)
            index = int(data.get("album_index") or 0)
            selected_slots = {
                datetime.fromisoformat(it["schedule_time"])
                for i, it in enumerate(items)
                if i != index and it.get("schedule_time")
            }
            free_times = _filter_selected_album_times(
                free_times,
                target_date,
                selected_slots,
            )

        if schedule_time.timetz().replace(tzinfo=None) not in free_times:
            await callback.answer(bot_content.message("slot_taken"), show_alert=True)
            return

        if single is not None:
            single["schedule_time"] = schedule_time.isoformat() if schedule_time else None
            single["is_auto_scheduled"] = False
        else:
            items[index]["schedule_time"] = schedule_time.isoformat() if schedule_time else None
            items[index]["is_auto_scheduled"] = False
            await state.update_data(album_items=items)

    await _render_dashboard(callback, state, bot)
    await callback.answer()


__all__ = [
    "handle_album_animal_navigation",
    "handle_animal_type",
    "handle_animal_type_back",
    "handle_calendar_day",
    "handle_calendar_nav",
    "handle_custom_animal_type_button",
    "handle_dash_album_auto_all",
    "handle_dash_change_type",
    "handle_dash_set_auto",
    "handle_dash_set_manual",
    "handle_dash_submit",
    "handle_dash_submit_album",
    "handle_extra_animal_type",
    "handle_manual_time",
    "handle_other_animal_type",
    "logger",
    "select_animal_type",
]
