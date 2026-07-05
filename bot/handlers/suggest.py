import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from uuid import uuid4

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InputMediaPhoto, Message

from bot.config import config
from bot.content import bot_content
from bot.keyboards.inline import (
    get_admin_album_kb,
    get_admin_approval_kb,
    get_animal_type_kb,
    get_other_animal_type_kb,
    get_schedule_choice_kb,
    get_time_slots_kb,
)
from bot.services.captions import (
    admin_album_control_text,
    album_submission_photo_caption,
    append_duplicate_note,
    format_schedule,
    submission_caption,
)
from bot.services.photo_storage import hamming_distance, upload_telegram_photo
from db.crud import (
    canonical_animal_type,
    combine_slot,
    create_photo,
    create_post,
    find_duplicate_photo,
    get_animal_type_name,
    get_day_availability,
    get_free_slot_times,
    get_next_auto_slot,
    get_or_create_user,
    get_photo_by_telegram_unique_id,
    now_in_app_tz,
    parse_daily_slot_times,
)
from db.database import async_session

suggest_router = Router()
logger = logging.getLogger(__name__)

ALBUM_COLLECTION_DELAY_SECONDS = 1.0


@dataclass
class AlbumBuffer:
    messages: list[Message]
    state: FSMContext
    bot: Bot
    task: asyncio.Task | None = None


_album_buffers: dict[tuple[int, str], AlbumBuffer] = {}
_album_lock = asyncio.Lock()


def user_display(user) -> str:
    if user.username:
        return f"@{user.username}"
    return str(user.id)


def _format_schedule(value: datetime) -> str:
    return format_schedule(value)


def _is_album_submission(data: dict) -> bool:
    return bool(data.get("is_album") and data.get("album_items"))


def _album_items(data: dict) -> list[dict]:
    return list(data.get("album_items") or [])


def _album_prompt_text(data: dict, *, include_warning: bool = False) -> str:
    items = _album_items(data)
    index = int(data.get("album_index") or 0)
    item = items[index] if items else {}
    text = bot_content.message(
        "album_photo_prompt",
        current=index + 1,
        total=len(items),
    )
    text = append_duplicate_note(
        text,
        item.get("duplicate_of_photo_id"),
        item.get("duplicate_distance"),
    )
    if include_warning:
        text += "\n\n" + bot_content.message("album_duplicate_warning")
    return text


def _single_photo_prompt_text(data: dict) -> str:
    return append_duplicate_note(
        bot_content.message("ask_animal_type"),
        data.get("duplicate_of_photo_id"),
        data.get("duplicate_distance"),
    )


def _album_selected_text(data: dict, animal_type: str) -> str:
    items = _album_items(data)
    index = int(data.get("album_index") or 0)
    return bot_content.message(
        "album_animal_type_selected",
        current=index + 1,
        total=len(items),
        animal_type=animal_type,
    )


def _album_animal_summary(items: list[dict]) -> str:
    return "\n".join(
        bot_content.message(
            "album_animal_type_summary_line",
            number=index,
            animal_type=item.get("animal_type") or "?",
        )
        for index, item in enumerate(items, start=1)
    )


def _album_schedule_summary(posts) -> str:
    return "\n".join(
        bot_content.message(
            "album_schedule_line",
            number=post.submission_group_index or index,
            animal_type=post.animal_type,
            schedule=_format_schedule(post.schedule_time),
        )
        for index, post in enumerate(posts, start=1)
    )


def _parse_album_schedule_time(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        return datetime.fromisoformat(value)
    return None


def _serialize_album_schedule_times(schedule_times: list[datetime | None]) -> list[str | None]:
    return [schedule_time.isoformat() if schedule_time else None for schedule_time in schedule_times]


def _album_schedule_context(data: dict) -> tuple[list[dict], list[datetime | None], list[bool], int]:
    items = _album_items(data)
    count = len(items)

    raw_times = list(data.get("album_schedule_times") or [])
    schedule_times = [_parse_album_schedule_time(value) for value in raw_times[:count]]
    schedule_times.extend([None] * (count - len(schedule_times)))

    raw_flags = list(data.get("album_schedule_auto_flags") or [])
    schedule_auto_flags = [bool(value) for value in raw_flags[:count]]
    schedule_auto_flags.extend([False] * (count - len(schedule_auto_flags)))

    try:
        schedule_index = int(data.get("album_schedule_index") or 0)
    except (TypeError, ValueError):
        schedule_index = 0

    if count:
        schedule_index = max(0, min(schedule_index, count - 1))
    else:
        schedule_index = 0

    return items, schedule_times, schedule_auto_flags, schedule_index


def _album_schedule_state(
    schedule_times: list[datetime | None],
    schedule_auto_flags: list[bool],
    schedule_index: int,
) -> dict:
    return {
        "album_schedule_times": _serialize_album_schedule_times(schedule_times),
        "album_schedule_auto_flags": schedule_auto_flags,
        "album_schedule_index": schedule_index,
    }


def _next_unscheduled_index(schedule_times: list[datetime | None], start_at: int = 0) -> int | None:
    for index in range(start_at, len(schedule_times)):
        if schedule_times[index] is None:
            return index

    for index in range(0, min(start_at, len(schedule_times))):
        if schedule_times[index] is None:
            return index

    return None


def _album_selected_slots(data: dict, *, exclude_index: int | None = None) -> set[datetime]:
    _, schedule_times, _, _ = _album_schedule_context(data)
    return {
        schedule_time
        for index, schedule_time in enumerate(schedule_times)
        if schedule_time is not None and index != exclude_index
    }


def _album_schedule_footer_buttons() -> list[tuple[str, str]]:
    return [
        (bot_content.button("album_schedule_auto_current"), "album_auto_current"),
        (bot_content.button("album_schedule_auto_remaining"), "album_auto_remaining"),
    ]


def _album_schedule_prompt_kwargs(data: dict) -> dict:
    items, _, _, schedule_index = _album_schedule_context(data)
    item = items[schedule_index] if items else {}
    return {
        "current": schedule_index + 1 if items else 0,
        "total": len(items),
        "animal_type": item.get("animal_type") or "?",
    }


def _subtract_selected_album_slots(availability: dict, selected_slots: set[datetime]) -> dict:
    adjusted = dict(availability)
    for schedule_time in selected_slots:
        target_date = schedule_time.date()
        if target_date in adjusted:
            adjusted[target_date] = max(adjusted[target_date] - 1, 0)
    return adjusted


def _filter_selected_album_times(
    free_times: list[time],
    target_date,
    selected_slots: set[datetime],
) -> list[time]:
    selected_times = {
        schedule_time.timetz().replace(tzinfo=None)
        for schedule_time in selected_slots
        if schedule_time.date() == target_date
    }
    return [slot_time for slot_time in free_times if slot_time not in selected_times]


async def _build_calendar_markup(data: dict, *, year: int, month: int):
    from bot.keyboards.calendar import build_month_calendar

    today = now_in_app_tz().date()
    min_date = today + timedelta(days=1)
    max_date = min_date + timedelta(days=config.AUTO_POST_DAYS_AHEAD - 1)

    async with async_session() as session:
        availability = await get_day_availability(session, start_date=min_date, days=config.AUTO_POST_DAYS_AHEAD)

    footer_buttons = None
    if _is_album_submission(data):
        _, _, _, schedule_index = _album_schedule_context(data)
        selected_slots = _album_selected_slots(data, exclude_index=schedule_index)
        availability = _subtract_selected_album_slots(availability, selected_slots)
        footer_buttons = _album_schedule_footer_buttons()

    return build_month_calendar(
        year=year,
        month=month,
        availability=availability,
        min_date=min_date,
        max_date=max_date,
        max_slots=len(parse_daily_slot_times()),
        footer_buttons=footer_buttons,
    )


async def _show_album_schedule_calendar(
    message: Message,
    data: dict,
    *,
    year: int | None = None,
    month: int | None = None,
    message_key: str = "choose_publication_date_album",
) -> None:
    today = now_in_app_tz().date()
    min_date = today + timedelta(days=1)
    year = year or min_date.year
    month = month or min_date.month

    await message.edit_text(
        bot_content.message(message_key, **_album_schedule_prompt_kwargs(data)),
        reply_markup=await _build_calendar_markup(data, year=year, month=month),
    )


class SuggestState(StatesGroup):
    waiting_for_animal_type = State()
    waiting_for_custom_animal_type = State()
    waiting_for_schedule_type = State()


async def _edit_callback_prompt(callback: CallbackQuery, text: str, reply_markup=None) -> None:
    if callback.message.photo:
        await callback.message.edit_caption(caption=text, reply_markup=reply_markup)
        return
    await callback.message.edit_text(text, reply_markup=reply_markup)


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
    sent = await bot.send_photo(
        chat_id=chat_id,
        photo=item["file_id"],
        caption=_album_prompt_text(data, include_warning=include_warning),
        reply_markup=await get_animal_type_kb(),
    )
    await state.update_data(
        album_prompt_chat_id=sent.chat.id,
        album_prompt_message_id=sent.message_id,
    )


async def _mark_album_prompt_selected(bot: Bot, state: FSMContext, text: str) -> None:
    data = await state.get_data()
    chat_id = data.get("album_prompt_chat_id")
    message_id = data.get("album_prompt_message_id")
    if not chat_id or not message_id:
        return

    try:
        await bot.edit_message_caption(chat_id=chat_id, message_id=message_id, caption=text)
    except TelegramAPIError:
        logger.exception("Failed to edit album prompt message %s", message_id)


async def _save_album_animal_type(state: FSMContext, animal_type: str) -> tuple[list[dict], int]:
    data = await state.get_data()
    items = _album_items(data)
    index = int(data.get("album_index") or 0)
    if not items or index >= len(items):
        raise RuntimeError("Album submission state is invalid")

    items[index] = {**items[index], "animal_type": animal_type}
    await state.update_data(album_items=items)
    return items, index


async def _continue_album_or_ask_schedule(bot: Bot, chat_id: int, state: FSMContext, items: list[dict], index: int) -> None:
    if index + 1 < len(items):
        await state.update_data(album_index=index + 1)
        await state.set_state(SuggestState.waiting_for_animal_type)
        await _send_album_item_prompt(bot, chat_id, state)
        return

    await state.set_state(SuggestState.waiting_for_schedule_type)
    await bot.send_message(
        chat_id=chat_id,
        text=bot_content.message(
            "album_animal_types_done",
            count=len(items),
            summary=_album_animal_summary(items),
        ),
        reply_markup=get_schedule_choice_kb(),
    )


async def _handle_album_animal_selected(
    callback: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    animal_type: str,
) -> None:
    items, index = await _save_album_animal_type(state, animal_type)
    data = await state.get_data()
    await _edit_callback_prompt(callback, _album_selected_text(data, animal_type))
    await _continue_album_or_ask_schedule(bot, callback.message.chat.id, state, items, index)
    await callback.answer()


async def _handle_album_custom_animal_type(message: Message, state: FSMContext, bot: Bot, animal_type: str) -> None:
    items, index = await _save_album_animal_type(state, animal_type)
    data = await state.get_data()
    await _mark_album_prompt_selected(bot, state, _album_selected_text(data, animal_type))
    await _continue_album_or_ask_schedule(bot, message.chat.id, state, items, index)


async def _find_next_free_slot(session, start_at: datetime, selected_slots: set[datetime]) -> datetime:
    max_days_to_scan = config.AUTO_POST_DAYS_AHEAD + 365
    current_date = start_at.date()

    for day_offset in range(max_days_to_scan):
        target_date = current_date + timedelta(days=day_offset)
        free_times = await get_free_slot_times(session, target_date)
        for slot_time in free_times:
            candidate = combine_slot(target_date, slot_time)
            if candidate < start_at or candidate in selected_slots:
                continue
            return candidate

    first_slot = parse_daily_slot_times()[0]
    return combine_slot(current_date + timedelta(days=max_days_to_scan), first_slot)


async def _allocate_album_schedule_slots(
    session,
    count: int,
    *,
    first_slot: datetime | None = None,
) -> list[datetime]:
    slots: list[datetime] = []
    selected_slots: set[datetime] = set()

    if first_slot is not None:
        slots.append(first_slot)
        selected_slots.add(first_slot)
        start_at = first_slot + timedelta(minutes=1)
    else:
        tomorrow = now_in_app_tz().date() + timedelta(days=1)
        start_at = combine_slot(tomorrow, time.min)

    while len(slots) < count:
        slot = await _find_next_free_slot(session, start_at, selected_slots)
        slots.append(slot)
        selected_slots.add(slot)
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
        ),
        reply_markup=get_admin_approval_kb(post.id),
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
    media = [
        InputMediaPhoto(
            media=post.file_id,
            caption=album_submission_photo_caption(post, post.submission_group_index or index),
        )
        for index, post in enumerate(ordered_posts, start=1)
    ]

    if len(media) == 1:
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

    await bot.send_media_group(chat_id=config.ADMIN_ID, media=media)
    await bot.send_message(
        chat_id=config.ADMIN_ID,
        text=admin_album_control_text(ordered_posts, author=author),
        reply_markup=get_admin_album_kb(ordered_posts),
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

        posts = await _create_album_posts(
            session,
            data=data,
            schedule_times=complete_schedule_times,
            schedule_auto_flags=schedule_auto_flags,
        )

    await state.clear()
    await callback.message.edit_text(
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


async def _process_single_photo_message(message: Message, state: FSMContext, bot: Bot) -> None:
    photo_size = message.photo[-1]
    file_id = photo_size.file_id
    file_unique_id = photo_size.file_unique_id

    try:
        user = await _get_or_create_submission_user(message)
        item = await _store_submitted_photo(bot, file_id=file_id, file_unique_id=file_unique_id)
    except Exception:
        logger.exception("Failed to store submitted photo")
        await message.answer(bot_content.message("photo_storage_failed"))
        return

    await state.clear()
    await state.update_data(
        is_album=False,
        file_id=file_id,
        photo_id=item["photo_id"],
        user_id=user.id,
        duplicate_of_photo_id=item.get("duplicate_of_photo_id"),
        duplicate_distance=item.get("duplicate_distance"),
    )
    data = await state.get_data()
    await state.set_state(SuggestState.waiting_for_animal_type)
    await message.reply(_single_photo_prompt_text(data), reply_markup=await get_animal_type_kb())


async def _process_album_messages(messages: list[Message], state: FSMContext, bot: Bot) -> None:
    messages = sorted(messages, key=lambda item: item.message_id)
    if len(messages) <= 1:
        await _process_single_photo_message(messages[0], state, bot)
        return

    try:
        user = await _get_or_create_submission_user(messages[0])
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


@suggest_router.message(F.photo)
async def handle_photo(message: Message, state: FSMContext, bot: Bot):
    if message.media_group_id:
        await _collect_album_message(message, state, bot)
        return

    await _process_single_photo_message(message, state, bot)


async def ask_for_schedule(message: Message, state: FSMContext, animal_type: str):
    await state.update_data(animal_type=animal_type)
    await state.set_state(SuggestState.waiting_for_schedule_type)
    await message.answer(
        bot_content.message("animal_type_selected", animal_type=animal_type),
        reply_markup=get_schedule_choice_kb(),
    )


async def select_animal_type(callback: CallbackQuery, state: FSMContext, bot: Bot, animal_type: str):
    data = await state.get_data()
    if _is_album_submission(data):
        await _handle_album_animal_selected(callback, state, bot, animal_type)
        return

    await state.update_data(animal_type=animal_type)
    await state.set_state(SuggestState.waiting_for_schedule_type)
    await _edit_callback_prompt(
        callback,
        bot_content.message("animal_type_selected", animal_type=animal_type),
        reply_markup=get_schedule_choice_kb(),
    )
    await callback.answer()


@suggest_router.callback_query(SuggestState.waiting_for_animal_type, F.data == "animal_other")
async def handle_other_animal_type(callback: CallbackQuery, state: FSMContext):
    await _edit_callback_prompt(
        callback,
        bot_content.message("choose_other_animal_type"),
        reply_markup=await get_other_animal_type_kb(),
    )
    await callback.answer()


@suggest_router.callback_query(SuggestState.waiting_for_animal_type, F.data == "animal_back")
async def handle_animal_type_back(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    text = _album_prompt_text(data) if _is_album_submission(data) else _single_photo_prompt_text(data)
    await _edit_callback_prompt(
        callback,
        text,
        reply_markup=await get_animal_type_kb(),
    )
    await callback.answer()


@suggest_router.callback_query(SuggestState.waiting_for_animal_type, F.data == "animal_custom")
async def handle_custom_animal_type_button(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SuggestState.waiting_for_custom_animal_type)
    await _edit_callback_prompt(callback, bot_content.message("ask_custom_animal_type"))
    await callback.answer()


@suggest_router.callback_query(SuggestState.waiting_for_animal_type, F.data.startswith("animal_id_"))
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


@suggest_router.callback_query(SuggestState.waiting_for_animal_type, F.data.startswith("animal_extra_id_"))
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


@suggest_router.message(SuggestState.waiting_for_custom_animal_type)
async def handle_custom_animal_type(message: Message, state: FSMContext, bot: Bot):
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
        await message.answer(
            bot_content.message("custom_animal_type_too_long", max_length=max_length)
        )
        return

    data = await state.get_data()
    if _is_album_submission(data):
        await _handle_album_custom_animal_type(message, state, bot, animal_type)
        return

    await ask_for_schedule(message, state, animal_type)


@suggest_router.callback_query(SuggestState.waiting_for_schedule_type, F.data == "schedule_auto")
async def handle_schedule_auto(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    author = user_display(callback.from_user)

    if _is_album_submission(data):
        items = _album_items(data)
        async with async_session() as session:
            schedule_times = await _allocate_album_schedule_slots(session, len(items))
            posts = await _create_album_posts(
                session,
                data=data,
                schedule_times=schedule_times,
                is_auto_scheduled=True,
            )

        await state.clear()
        await callback.message.edit_text(
            bot_content.message(
                "album_submitted_auto",
                schedules=_album_schedule_summary(posts),
            )
        )
        await _send_album_submission_to_admin(bot, posts=posts, author=author)
        await callback.answer()
        return

    file_id = data.get("file_id")
    photo_id = data.get("photo_id")
    duplicate_of_photo_id = data.get("duplicate_of_photo_id")
    duplicate_distance = data.get("duplicate_distance")
    animal_type = data.get("animal_type")
    user_id = data.get("user_id")

    async with async_session() as session:
        schedule_time = await get_next_auto_slot(session)
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

    await state.clear()
    await callback.message.edit_text(
        bot_content.message(
            "photo_submitted_auto",
            schedule=_format_schedule(schedule_time),
        )
    )

    await _send_single_submission_to_admin(
        bot,
        post=post,
        file_id=file_id,
        animal_type=animal_type,
        schedule_time=schedule_time,
        author=author,
    )
    await callback.answer()


@suggest_router.callback_query(SuggestState.waiting_for_schedule_type, F.data == "schedule_manual")
async def handle_schedule_manual(callback: CallbackQuery, state: FSMContext):
    today = now_in_app_tz().date()
    min_date = today + timedelta(days=1)
    data = await state.get_data()

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

    await callback.message.edit_text(
        bot_content.message("choose_publication_date"),
        reply_markup=await _build_calendar_markup(data, year=min_date.year, month=min_date.month),
    )
    await callback.answer()


@suggest_router.callback_query(SuggestState.waiting_for_schedule_type, F.data.startswith("cal_nav_"))
async def handle_calendar_nav(callback: CallbackQuery, state: FSMContext):
    _, _, year_raw, month_raw = callback.data.split("_")
    year = int(year_raw)
    month = int(month_raw)
    today = now_in_app_tz().date()
    min_date = today + timedelta(days=1)
    max_date = min_date + timedelta(days=config.AUTO_POST_DAYS_AHEAD - 1)
    shown_date = datetime(year=year, month=month, day=1).date()
    if shown_date < min_date.replace(day=1) or shown_date > max_date.replace(day=1):
        await callback.answer()
        return

    data = await state.get_data()
    await callback.message.edit_reply_markup(
        reply_markup=await _build_calendar_markup(data, year=year, month=month)
    )
    await callback.answer()


@suggest_router.callback_query(SuggestState.waiting_for_schedule_type, F.data.startswith("cal_day_"))
async def handle_calendar_day(callback: CallbackQuery, state: FSMContext):
    _, _, day_raw = callback.data.split("_")
    target_date = datetime.strptime(day_raw, "%Y-%m-%d").date()

    async with async_session() as session:
        free_times = await get_free_slot_times(session, target_date)

    if not free_times:
        await callback.answer(bot_content.message("no_free_slots"), show_alert=True)
        return

    data = await state.get_data()
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

    await callback.message.edit_text(
        bot_content.message(message_key, **message_kwargs),
        reply_markup=get_time_slots_kb(target_date, free_times, footer_buttons=footer_buttons),
    )
    await callback.answer()


@suggest_router.callback_query(SuggestState.waiting_for_schedule_type, F.data.startswith("time_"))
async def handle_manual_time(callback: CallbackQuery, state: FSMContext, bot: Bot):
    _, day_raw, time_raw = callback.data.split("_")
    target_date = datetime.strptime(day_raw, "%Y-%m-%d").date()
    slot_hour, slot_minute = [int(part) for part in time_raw.split(":", 1)]
    schedule_time = combine_slot(target_date, datetime.min.time().replace(hour=slot_hour, minute=slot_minute))

    data = await state.get_data()
    author = user_display(callback.from_user)

    async with async_session() as session:
        free_times = await get_free_slot_times(session, target_date)
        if _is_album_submission(data):
            _, _, _, schedule_index = _album_schedule_context(data)
            free_times = _filter_selected_album_times(
                free_times,
                target_date,
                _album_selected_slots(data, exclude_index=schedule_index),
            )

        if schedule_time.timetz().replace(tzinfo=None) not in free_times:
            await callback.answer(bot_content.message("slot_taken"), show_alert=True)
            return

        if _is_album_submission(data):
            await _save_album_schedule_and_continue(
                callback,
                state,
                bot,
                schedule_time=schedule_time,
                is_auto_scheduled=False,
            )
            await callback.answer()
            return
        else:
            file_id = data.get("file_id")
            photo_id = data.get("photo_id")
            duplicate_of_photo_id = data.get("duplicate_of_photo_id")
            duplicate_distance = data.get("duplicate_distance")
            animal_type = data.get("animal_type")
            user_id = data.get("user_id")

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

    await state.clear()

    await callback.message.edit_text(
        bot_content.message(
            "photo_submitted_manual",
            schedule=_format_schedule(schedule_time),
        )
    )

    await _send_single_submission_to_admin(
        bot,
        post=post,
        file_id=data.get("file_id"),
        animal_type=data.get("animal_type"),
        schedule_time=schedule_time,
        author=author,
    )
    await callback.answer()


@suggest_router.callback_query(SuggestState.waiting_for_schedule_type, F.data == "album_auto_current")
async def handle_album_auto_current(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    if not _is_album_submission(data):
        await callback.answer()
        return

    _, _, _, schedule_index = _album_schedule_context(data)
    selected_slots = _album_selected_slots(data, exclude_index=schedule_index)
    tomorrow = now_in_app_tz().date() + timedelta(days=1)
    async with async_session() as session:
        schedule_time = await _find_next_free_slot(
            session,
            combine_slot(tomorrow, time.min),
            selected_slots,
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
    tomorrow = now_in_app_tz().date() + timedelta(days=1)
    start_at = combine_slot(tomorrow, time.min)

    async with async_session() as session:
        for index in remaining_indices:
            schedule_time = await _find_next_free_slot(session, start_at, selected_slots)
            schedule_times[index] = schedule_time
            schedule_auto_flags[index] = True
            selected_slots.add(schedule_time)
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


@suggest_router.callback_query(SuggestState.waiting_for_schedule_type, F.data == "noop")
async def handle_noop(callback: CallbackQuery):
    await callback.answer()
