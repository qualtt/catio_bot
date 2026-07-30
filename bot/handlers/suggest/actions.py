import logging
from datetime import date, datetime, time, timedelta
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InputMediaPhoto, Message

from bot.config import config
from bot.content import bot_content
from bot.keyboards.inline import (
    get_admin_album_view_kb,
    get_admin_approval_kb,
    get_animal_type_kb,
    get_schedule_choice_kb,
)
from bot.services.captions import (
    admin_album_view_caption,
    submission_caption,
)
from bot.services.photo_storage import hamming_distance, upload_telegram_photo
from db.crud import (
    combine_slot,
    create_photo,
    create_post,
    find_duplicate_photo,
    get_free_slot_times,
    get_next_auto_slot,
    get_or_create_user,
    get_photo_by_telegram_unique_id,
    is_cat_animal_type,
    now_in_app_tz,
)
from db.database import async_session

from .buffer import *
from .helpers import *
from .router import SuggestState

logger = logging.getLogger(__name__)


async def _select_single_animal_type(callback: CallbackQuery, animal_type: str) -> None:
    message_id = callback.message.message_id
    single = _get_single_submission(message_id)
    if single is None:
        await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
        return

    single["animal_type"] = animal_type
    single["stage"] = "schedule"
    async with async_session() as session:
        next_slot = await get_next_auto_slot(session, animal_type=animal_type)
    auto_date = next_slot.strftime("%d.%m") if next_slot else None

    await _edit_callback_prompt(
        callback,
        bot_content.message("animal_type_selected", animal_type=animal_type),
        reply_markup=get_schedule_choice_kb(auto_date=auto_date),
    )
    await callback.answer()


async def _ask_single_schedule(bot: Bot, *, chat_id: int, message_id: int, animal_type: str) -> None:
    single = _get_single_submission(message_id)
    if single is None:
        return

    single["animal_type"] = animal_type
    single["stage"] = "schedule"
    _custom_animal_prompt_by_user.pop(single.get("user_id"), None)
    async with async_session() as session:
        next_slot = await get_next_auto_slot(session, animal_type=animal_type)
    auto_date = next_slot.strftime("%d.%m") if next_slot else None

    await _edit_bot_message_text_or_caption(
        bot,
        chat_id=chat_id,
        message_id=message_id,
        text=bot_content.message("animal_type_selected", animal_type=animal_type),
        reply_markup=get_schedule_choice_kb(auto_date=auto_date),
    )


async def _get_or_create_submission_user(message: Message):
    async with async_session() as session:
        return await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name,
        )


async def _store_submitted_photo(bot: Bot, *, file_id: str, file_unique_id: str | None) -> dict:
    async with async_session() as session:
        photo = await get_photo_by_telegram_unique_id(session, file_unique_id)

    if photo is None:
        stored_photo = await upload_telegram_photo(
            bot,
            file_id=file_id,
            file_unique_id=file_unique_id,
            source="submissions",
        )
        async with async_session() as session:
            photo = await create_photo(
                session,
                telegram_file_id=stored_photo.telegram_file_id,
                telegram_file_unique_id=stored_photo.telegram_file_unique_id,
                storage_bucket=stored_photo.storage_bucket,
                storage_key=stored_photo.storage_key,
                content_type=stored_photo.content_type,
                file_size=stored_photo.file_size,
                sha256=stored_photo.sha256,
                perceptual_hash=stored_photo.perceptual_hash,
            )

    async with async_session() as session:
        photo = await session.merge(photo)
        duplicate_match = await find_duplicate_photo(session, photo)

    return {
        "file_id": file_id,
        "photo_id": photo.id,
        "duplicate_of_photo_id": duplicate_match.photo_id if duplicate_match else None,
        "duplicate_distance": duplicate_match.distance if duplicate_match else None,
        "sha256": photo.sha256,
        "perceptual_hash": photo.perceptual_hash,
    }


def _set_better_duplicate_match(item: dict, duplicate_photo_id: int, distance: int) -> None:
    current_distance = item.get("duplicate_distance")
    if current_distance is None or distance < current_distance:
        item["duplicate_of_photo_id"] = duplicate_photo_id
        item["duplicate_distance"] = distance


def _annotate_album_internal_duplicates(items: list[dict]) -> None:
    for index, item in enumerate(items):
        for previous in items[:index]:
            if item.get("photo_id") == previous.get("photo_id"):
                _set_better_duplicate_match(item, previous["photo_id"], 0)
                continue

            if item.get("sha256") and item.get("sha256") == previous.get("sha256"):
                _set_better_duplicate_match(item, previous["photo_id"], 0)
                continue

            distance = hamming_distance(item.get("perceptual_hash"), previous.get("perceptual_hash"))
            if distance is not None and distance <= config.DUPLICATE_PHASH_MAX_DISTANCE:
                _set_better_duplicate_match(item, previous["photo_id"], distance)


async def _send_album_item_prompt(
    bot: Bot,
    chat_id: int,
    state: FSMContext,
    *,
    include_warning: bool = False,
) -> None:
    data = await state.get_data()
    items = _album_items(data)
    index = int(data.get("album_index") or 0)
    item = items[index]
    caption = _album_prompt_text(data, include_warning=include_warning)
    
    gemini = item.get("gemini")
    if gemini and not item.get("gemini_rejected"):
        from bot.keyboards.inline import get_gemini_confirmation_kb
        reply_markup = get_gemini_confirmation_kb(is_valid=gemini.get("is_valid", False), with_album_nav=True)
    else:
        reply_markup = await get_animal_type_kb(with_album_nav=True)
        
    prompt_chat_id = data.get("album_prompt_chat_id")
    prompt_message_id = data.get("album_prompt_message_id")
    if prompt_chat_id and prompt_message_id:
        await bot.edit_message_media(
            chat_id=prompt_chat_id,
            message_id=prompt_message_id,
            media=InputMediaPhoto(media=item["file_id"], caption=caption),
            reply_markup=reply_markup,
        )
        return

    sent = await bot.send_photo(chat_id=chat_id, photo=item["file_id"], caption=caption, reply_markup=reply_markup)
    await state.update_data(
        album_prompt_chat_id=sent.chat.id,
        album_prompt_message_id=sent.message_id,
    )


async def _edit_album_prompt_caption(bot: Bot, state: FSMContext, text: str, reply_markup=None) -> bool:
    data = await state.get_data()
    chat_id = data.get("album_prompt_chat_id")
    message_id = data.get("album_prompt_message_id")
    if not chat_id or not message_id:
        return False

    try:
        await bot.edit_message_caption(chat_id=chat_id, message_id=message_id, caption=text, reply_markup=reply_markup)
        return True
    except TelegramAPIError:
        logger.exception("Failed to edit album prompt message %s", message_id)
        return False


async def _save_album_animal_type(state: FSMContext, animal_type: str) -> tuple[list[dict], int]:
    data = await state.get_data()
    items = _album_items(data)
    index = int(data.get("album_index") or 0)
    if not items or index >= len(items):
        raise RuntimeError("Album submission state is invalid")

    items[index] = {**items[index], "animal_type": animal_type}
    await state.update_data(album_items=items)
    return items, index


async def _continue_album_or_ask_schedule(
    bot: Bot,
    chat_id: int,
    state: FSMContext,
    items: list[dict],
    index: int,
    source_message: Message | None = None,
) -> None:
    next_index = _next_untyped_album_index(items, index + 1)
    if next_index is not None:
        await state.update_data(album_index=next_index)
        await state.set_state(SuggestState.waiting_for_animal_type)
        await _send_album_item_prompt(bot, chat_id, state)
        return

    await state.set_state(SuggestState.waiting_for_schedule_type)
    text = bot_content.message(
        "album_animal_types_done",
        count=len(items),
        summary=_album_animal_summary(items),
    )
    auto_date = None
    if items:
        async with async_session() as session:
            next_slot = await get_next_auto_slot(session, animal_type=items[0].get("animal_type"))
        auto_date = next_slot.strftime("%d.%m") if next_slot else None

    if source_message:
        await _edit_message_text_or_caption(source_message, text, reply_markup=get_schedule_choice_kb(auto_date=auto_date))
    elif not await _edit_album_prompt_caption(bot, state, text, reply_markup=get_schedule_choice_kb(auto_date=auto_date)):
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=get_schedule_choice_kb(auto_date=auto_date))


async def _handle_album_animal_selected(
    callback: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    animal_type: str,
) -> None:
    items, index = await _save_album_animal_type(state, animal_type)
    await _continue_album_or_ask_schedule(
        bot,
        callback.message.chat.id,
        state,
        items,
        index,
        source_message=callback.message,
    )
    await callback.answer()


async def _handle_album_custom_animal_type(message: Message, state: FSMContext, bot: Bot, animal_type: str) -> None:
    items, index = await _save_album_animal_type(state, animal_type)
    await _continue_album_or_ask_schedule(bot, message.chat.id, state, items, index)


def _album_selected_cat_dates(
    items: list[dict],
    schedule_times: list[datetime | None],
    *,
    exclude_indices: set[int] | None = None,
) -> set[date]:
    exclude_indices = exclude_indices or set()
    return {
        schedule_time.date()
        for index, schedule_time in enumerate(schedule_times)
        if schedule_time is not None
        and index not in exclude_indices
        and index < len(items)
        and is_cat_animal_type(items[index].get("animal_type"))
    }


async def _find_next_auto_slot(
    session,
    *,
    animal_type: str | None,
    start_at: datetime,
    selected_slots: set[datetime],
    selected_cat_dates: set[date],
) -> datetime:
    return await get_next_auto_slot(
        session,
        animal_type=animal_type,
        start_at=start_at,
        selected_slots=selected_slots,
        selected_cat_dates=selected_cat_dates,
    )


async def _allocate_album_schedule_slots(
    session,
    items: list[dict],
    *,
    first_slot: datetime | None = None,
) -> list[datetime]:
    slots: list[datetime] = []
    selected_slots: set[datetime] = set()
    selected_cat_dates: set[date] = set()

    if first_slot is not None:
        slots.append(first_slot)
        selected_slots.add(first_slot)
        if items and is_cat_animal_type(items[0].get("animal_type")):
            selected_cat_dates.add(first_slot.date())
        start_at = first_slot + timedelta(minutes=1)
    else:
        tomorrow = now_in_app_tz().date() + timedelta(days=1)
        start_at = combine_slot(tomorrow, time.min)

    while len(slots) < len(items):
        animal_type = items[len(slots)].get("animal_type")
        slot = await _find_next_auto_slot(
            session,
            animal_type=animal_type,
            start_at=start_at,
            selected_slots=selected_slots,
            selected_cat_dates=selected_cat_dates,
        )
        slots.append(slot)
        selected_slots.add(slot)
        if is_cat_animal_type(animal_type):
            selected_cat_dates.add(slot.date())
        start_at = slot + timedelta(minutes=1)

    return slots


async def _create_album_posts(
    session,
    *,
    data: dict,
    schedule_times: list[datetime],
    is_auto_scheduled: bool | None = None,
    schedule_auto_flags: list[bool] | None = None,
) -> list:
    items = _album_items(data)
    group_id = data.get("submission_group_id") or f"album-{uuid4().hex}"
    if schedule_auto_flags is None:
        schedule_auto_flags = [bool(is_auto_scheduled)] * len(items)
    if len(schedule_times) != len(items) or len(schedule_auto_flags) != len(items):
        raise RuntimeError("Album schedule state is invalid")
    posts = []

    for index, (item, schedule_time) in enumerate(zip(items, schedule_times), start=1):
        post = await create_post(
            session,
            user_id=data["user_id"],
            file_id=item["file_id"],
            animal_type=item.get("animal_type"),
            is_auto_scheduled=schedule_auto_flags[index - 1],
            manual_time=schedule_time,
            photo_id=item.get("photo_id"),
            duplicate_of_photo_id=item.get("duplicate_of_photo_id"),
            duplicate_distance=item.get("duplicate_distance"),
            submission_group_id=group_id,
            submission_group_index=index,
            submission_group_size=len(items),
        )
        await session.refresh(post, ["duplicate_of_photo"])
        posts.append(post)

    return posts


async def _send_single_submission_to_admin(
    bot: Bot,
    *,
    post,
    file_id: str,
    animal_type: str,
    schedule_time: datetime,
    author: str,
    ai_comment: str | None = None,
) -> None:
    await bot.send_photo(
        chat_id=config.ADMIN_ID,
        photo=file_id,
        caption=submission_caption(
            animal_type=animal_type,
            schedule=_format_schedule(schedule_time),
            author=author,
            duplicate_of_photo_id=post.duplicate_of_photo_id,
            duplicate_distance=post.duplicate_distance,
            ai_comment=ai_comment,
        ),
        reply_markup=get_admin_approval_kb(post.id, post.user_id),
    )
    await _send_duplicate_original_to_admin(bot, post=post)


async def _send_duplicate_original_to_admin(bot: Bot, *, post) -> None:
    if post.duplicate_of_photo_id is None:
        return

    original = getattr(post, "duplicate_of_photo", None)
    if original is None or not original.telegram_file_id:
        await bot.send_message(
            chat_id=config.ADMIN_ID,
            text=bot_content.message(
                "admin_duplicate_original_unavailable",
                post_id=post.id,
                photo_id=post.duplicate_of_photo_id,
            ),
        )
        return

    await bot.send_photo(
        chat_id=config.ADMIN_ID,
        photo=original.telegram_file_id,
        caption=bot_content.message(
            "admin_duplicate_original_caption",
            post_id=post.id,
            photo_id=post.duplicate_of_photo_id,
        ),
    )


async def _send_album_submission_to_admin(bot: Bot, *, posts: list, author: str) -> None:
    ordered_posts = sorted(posts, key=lambda post: post.submission_group_index or post.id)

    if len(ordered_posts) == 1:
        post = ordered_posts[0]
        await _send_single_submission_to_admin(
            bot,
            post=post,
            file_id=post.file_id,
            animal_type=post.animal_type,
            schedule_time=post.schedule_time,
            author=author,
        )
        return

    first_post = ordered_posts[0]
    await bot.send_photo(
        chat_id=config.ADMIN_ID,
        photo=first_post.file_id,
        caption=admin_album_view_caption(ordered_posts, first_post, author=author),
        reply_markup=get_admin_album_view_kb(ordered_posts, first_post),
    )
    for post in ordered_posts:
        await _send_duplicate_original_to_admin(bot, post=post)


async def _first_album_schedule_conflict(session, schedule_times: list[datetime]) -> int | None:
    selected_slot_keys = set()
    for index, schedule_time in enumerate(schedule_times):
        slot_key = (schedule_time.date(), schedule_time.timetz().replace(tzinfo=None))
        if slot_key in selected_slot_keys:
            return index
        selected_slot_keys.add(slot_key)

    for index, schedule_time in enumerate(schedule_times):
        free_times = await get_free_slot_times(session, schedule_time.date())
        if schedule_time.timetz().replace(tzinfo=None) not in free_times:
            return index

    return None


async def _finalize_album_submission(
    callback: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    *,
    data: dict,
    schedule_times: list[datetime | None],
    schedule_auto_flags: list[bool],
) -> None:
    complete_schedule_times = [schedule_time for schedule_time in schedule_times if schedule_time is not None]
    if len(complete_schedule_times) != len(_album_items(data)):
        missing_index = _next_unscheduled_index(schedule_times) or 0
        await state.update_data(**_album_schedule_state(schedule_times, schedule_auto_flags, missing_index))
        updated_data = {
            **data,
            **_album_schedule_state(schedule_times, schedule_auto_flags, missing_index),
        }
        await _show_album_schedule_calendar(callback.message, updated_data)
        return

    async with async_session() as session:
        conflict_index = await _first_album_schedule_conflict(session, complete_schedule_times)
        if conflict_index is not None:
            schedule_times[conflict_index] = None
            schedule_auto_flags[conflict_index] = False
            state_data = _album_schedule_state(schedule_times, schedule_auto_flags, conflict_index)
            await state.update_data(**state_data)
            await _show_album_schedule_calendar(
                callback.message,
                {**data, **state_data},
                message_key="album_slot_taken",
            )
            return

        try:
            posts = await _create_album_posts(
                session,
                data=data,
                schedule_times=complete_schedule_times,
                schedule_auto_flags=schedule_auto_flags,
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
            "album_submitted_manual",
            schedules=_album_schedule_summary(posts),
        )
    )
    await _send_album_submission_to_admin(bot, posts=posts, author=user_display(callback.from_user))


async def _save_album_schedule_and_continue(
    callback: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    *,
    schedule_time: datetime,
    is_auto_scheduled: bool,
) -> None:
    data = await state.get_data()
    items, schedule_times, schedule_auto_flags, schedule_index = _album_schedule_context(data)
    if not items:
        await callback.answer()
        return

    schedule_times[schedule_index] = schedule_time
    schedule_auto_flags[schedule_index] = is_auto_scheduled
    next_index = _next_unscheduled_index(schedule_times, schedule_index + 1)

    if next_index is not None:
        state_data = _album_schedule_state(schedule_times, schedule_auto_flags, next_index)
        await state.update_data(**state_data)
        await _show_album_schedule_calendar(callback.message, {**data, **state_data})
        return

    await _finalize_album_submission(
        callback,
        state,
        bot,
        data=data,
        schedule_times=schedule_times,
        schedule_auto_flags=schedule_auto_flags,
    )



__all__ = ['_album_selected_cat_dates', '_allocate_album_schedule_slots', '_annotate_album_internal_duplicates', '_ask_single_schedule', '_continue_album_or_ask_schedule', '_create_album_posts', '_edit_album_prompt_caption', '_finalize_album_submission', '_find_next_auto_slot', '_first_album_schedule_conflict', '_get_or_create_submission_user', '_handle_album_animal_selected', '_handle_album_custom_animal_type', '_save_album_animal_type', '_save_album_schedule_and_continue', '_select_single_animal_type', '_send_album_item_prompt', '_send_album_submission_to_admin', '_send_duplicate_original_to_admin', '_send_single_submission_to_admin', '_set_better_duplicate_match', '_store_submitted_photo', 'logger']
