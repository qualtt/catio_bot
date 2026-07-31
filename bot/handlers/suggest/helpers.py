import logging
from datetime import datetime, time, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import CallbackQuery, Message

from bot.config import config
from bot.content import bot_content
from bot.services.captions import (
    append_duplicate_note,
    format_schedule,
)
from db.crud import (
    animal_type_has_unsupported_latin,
    canonical_animal_type,
    get_day_availability,
    now_in_app_tz,
    parse_daily_slot_times,
)
from db.database import async_session

from .buffer import *

logger = logging.getLogger(__name__)


def user_display(user) -> str:
    if user.username:
        return f"@{user.username}"
    return str(user.id)


def _format_schedule(value) -> str:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return format_schedule(value)


def _is_album_submission(data: dict) -> bool:
    return bool(data.get("is_album") and data.get("album_items"))


def _album_items(data: dict) -> list[dict]:
    return list(data.get("album_items") or [])


def _format_dashboard_time(item: dict) -> str:
    if item.get("is_auto_scheduled"):
        if item.get("schedule_time"):
            return f"Автоматически ({_format_schedule(item['schedule_time'])})"
        return "Автоматически"
    if item.get("schedule_time"):
        return _format_schedule(item["schedule_time"])
    return "Не выбрано"


def _photo_dashboard_text(data: dict, *, is_album: bool) -> str:
    if is_album:
        items = _album_items(data)
        index = int(data.get("album_index") or 0)
        item = items[index] if items else {}
        total = len(items)
        display_index = index + 1
    else:
        item = data
        total = 1
        display_index = 1

    gemini = item.get("gemini")
    gemini_text = ""
    if gemini:
        if gemini.get("is_valid"):
            gemini_text = f"🤖 Нейросеть: {gemini.get('animal')}"
        else:
            gemini_text = f"🤖 Нейросеть отклонила: {gemini.get('reason')}"
        if gemini.get("comment"):
            gemini_text += f"\n💬 Комментарий ИИ: {gemini.get('comment')}"
    else:
        gemini_text = "🤖 Нейросеть: Ожидание..."

    animal_type = item.get("animal_type") or "Не выбран"
    schedule_time = _format_dashboard_time(item)

    text = bot_content.message(
        "dashboard_caption",
        index=display_index,
        total=total,
        gemini=gemini_text,
        animal_type=animal_type,
        schedule_time=schedule_time,
    )

    # Remove index header for single photos
    if not is_album:
        text = text.replace(f"📸 Фото {display_index} из {total}\n\n", "")

    return append_duplicate_note(
        text,
        item.get("duplicate_of_photo_id"),
        item.get("duplicate_distance"),
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


def _serialize_album_schedule_times(
    schedule_times: list[datetime | None],
) -> list[str | None]:
    return [schedule_time.isoformat() if schedule_time else None for schedule_time in schedule_times]


def _album_schedule_context(
    data: dict,
) -> tuple[list[dict], list[datetime | None], list[bool], int]:
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

    for index in range(min(start_at, len(schedule_times))):
        if schedule_times[index] is None:
            return index

    return None


def _next_untyped_album_index(items: list[dict], start_at: int = 0) -> int | None:
    for index in range(start_at, len(items)):
        if not items[index].get("animal_type"):
            return index

    for index in range(min(start_at, len(items))):
        if not items[index].get("animal_type"):
            return index

    return None


def _album_selected_slots(data: dict, *, exclude_index: int | None = None) -> set[datetime]:
    _, schedule_times, _, _ = _album_schedule_context(data)
    return {
        schedule_time
        for index, schedule_time in enumerate(schedule_times)
        if schedule_time is not None and index != exclude_index
    }


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

    await _edit_message_text_or_caption(
        message,
        bot_content.message(message_key, **_album_schedule_prompt_kwargs(data)),
        reply_markup=await _build_calendar_markup(data, year=year, month=month),
    )


async def _edit_callback_prompt(callback: CallbackQuery, text: str, reply_markup=None) -> None:
    try:
        if callback.message.photo:
            await callback.message.edit_caption(caption=text, reply_markup=reply_markup)
            return
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower():
            logger.debug("Ignored message is not modified error for callback prompt")
        else:
            raise


async def _edit_message_text_or_caption(message: Message, text: str, reply_markup=None) -> None:
    try:
        if message.photo:
            await message.edit_caption(caption=text, reply_markup=reply_markup)
            return
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower():
            logger.debug("Ignored message is not modified error for edit message")
        else:
            raise


async def _edit_bot_message_text_or_caption(
    bot: Bot,
    *,
    chat_id: int,
    message_id: int,
    text: str,
    reply_markup=None,
) -> None:
    try:
        await bot.edit_message_caption(
            chat_id=chat_id,
            message_id=message_id,
            caption=text,
            reply_markup=reply_markup,
        )
        return
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower():
            return
        # If it's not a caption, it will raise another BadRequest, so we fall through
    except TelegramAPIError:
        pass

    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=reply_markup,
        )
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower():
            return
        raise


async def _normalize_custom_animal_type_text(message: Message) -> str | None:
    max_length = bot_content.animal_type_max_length()
    if animal_type_has_unsupported_latin(message.text):
        await message.answer(bot_content.message("invalid_custom_animal_type_layout"))
        return None

    async with async_session() as session:
        animal_type = await canonical_animal_type(session, message.text)

    if not animal_type:
        await message.answer(bot_content.message("invalid_custom_animal_type"))
        return None

    if animal_type.casefold() == bot_content.other_animal_label().casefold():
        await message.answer(bot_content.message("invalid_custom_animal_type"))
        return None

    if len(animal_type) > max_length:
        await message.answer(bot_content.message("custom_animal_type_too_long", max_length=max_length))
        return None

    return animal_type


__all__ = [
    "_album_animal_summary",
    "_album_items",
    "_album_schedule_context",
    "_album_schedule_prompt_kwargs",
    "_album_schedule_state",
    "_album_schedule_summary",
    "_album_selected_slots",
    "_build_calendar_markup",
    "_edit_bot_message_text_or_caption",
    "_edit_callback_prompt",
    "_edit_message_text_or_caption",
    "_filter_selected_album_times",
    "_format_schedule",
    "_is_album_submission",
    "_next_unscheduled_index",
    "_next_untyped_album_index",
    "_normalize_custom_animal_type_text",
    "_parse_album_schedule_time",
    "_photo_dashboard_text",
    "_serialize_album_schedule_times",
    "_show_album_schedule_calendar",
    "_subtract_selected_album_slots",
    "logger",
    "user_display",
]
