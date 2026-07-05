import calendar
from datetime import date

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.content import bot_content


def slot_marker(free_slots: int, max_slots: int) -> str:
    if free_slots <= 0:
        return "⬛️"

    if max_slots <= 1:
        return "🟩"

    ratio = free_slots / max_slots
    if ratio <= 0.34:
        return "🟥"
    if ratio <= 0.6:
        return "🟨"
    return "🟩"


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    month_index = (year * 12 + month - 1) + delta
    return month_index // 12, month_index % 12 + 1


def _month_start(target_date: date) -> date:
    return target_date.replace(day=1)


def build_month_calendar(
    *,
    year: int,
    month: int,
    availability: dict[date, int],
    min_date: date,
    max_date: date,
    max_slots: int,
    footer_buttons: list[tuple[str, str]] | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    calendar.setfirstweekday(calendar.MONDAY)

    current_month = date(year, month, 1)
    month_weeks = calendar.monthcalendar(year, month)
    prev_year, prev_month = _shift_month(year, month, -1)
    next_year, next_month = _shift_month(year, month, 1)
    prev_enabled = date(prev_year, prev_month, 1) >= _month_start(min_date)
    next_enabled = date(next_year, next_month, 1) <= _month_start(max_date)

    builder.button(
        text="‹",
        callback_data=f"cal_nav_{prev_year}_{prev_month}" if prev_enabled else "noop",
    )
    builder.button(text=f"{bot_content.month_name(month)} {year}", callback_data="noop")
    builder.button(
        text="›",
        callback_data=f"cal_nav_{next_year}_{next_month}" if next_enabled else "noop",
    )

    for weekday in bot_content.weekday_names():
        builder.button(text=weekday, callback_data="noop")

    for week in month_weeks:
        for day_number in week:
            if day_number == 0:
                builder.button(text=" ", callback_data="noop")
                continue

            day = current_month.replace(day=day_number)
            free_slots = availability.get(day, 0)
            marker = slot_marker(free_slots, max_slots)
            enabled = min_date <= day <= max_date and free_slots > 0
            callback_data = f"cal_day_{day.isoformat()}" if enabled else "noop"
            builder.button(text=f"{marker} {day_number}", callback_data=callback_data)

    footer_buttons = footer_buttons or []
    for text, callback_data in footer_buttons:
        builder.button(text=text, callback_data=callback_data)

    builder.adjust(3, 7, *([7] * len(month_weeks)), *([1] * len(footer_buttons)))
    return builder.as_markup()
