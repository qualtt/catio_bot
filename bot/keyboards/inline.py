from datetime import date, time

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.content import bot_content
from db.crud import AnimalTypeOption, get_animal_type_options
from db.database import async_session


def _two_column_rows(item_count: int, footer_count: int = 0) -> list[int]:
    rows = [2] * (item_count // 2)
    if item_count % 2:
        rows.append(1)
    rows.extend([1] * footer_count)
    return rows or [1]


async def get_animal_type_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    async with async_session() as session:
        animal_types = await get_animal_type_options(session, is_primary=True)

    for animal_type in animal_types:
        builder.button(text=animal_type.name, callback_data=f"animal_id_{animal_type.id}")
    builder.button(text=bot_content.other_animal_label(), callback_data="animal_other")
    builder.adjust(*_two_column_rows(len(animal_types), footer_count=1))
    return builder.as_markup()


async def get_other_animal_type_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    async with async_session() as session:
        animal_types = await get_animal_type_options(session, is_primary=False)

    for animal_type in animal_types:
        builder.button(text=animal_type.name, callback_data=f"animal_extra_id_{animal_type.id}")
    builder.button(text=bot_content.button("custom_animal_type"), callback_data="animal_custom")
    builder.button(text=bot_content.button("back"), callback_data="animal_back")
    builder.adjust(*_two_column_rows(len(animal_types), footer_count=2))
    return builder.as_markup()

def get_schedule_choice_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=bot_content.button("schedule_auto"), callback_data="schedule_auto")
    builder.button(text=bot_content.button("schedule_manual"), callback_data="schedule_manual")
    builder.adjust(1)
    return builder.as_markup()

def get_admin_approval_kb(post_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=bot_content.button("approve"), callback_data=f"admin_approve_{post_id}")
    builder.button(text=bot_content.button("reject"), callback_data=f"admin_reject_{post_id}")
    builder.button(text=bot_content.button("change_animal"), callback_data=f"admin_change_{post_id}")
    builder.adjust(2, 1)
    return builder.as_markup()


async def get_admin_animal_change_kb(post_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    async with async_session() as session:
        primary_types = await get_animal_type_options(session, is_primary=True)
        other_types = await get_animal_type_options(session, is_primary=False)
    animal_types: list[AnimalTypeOption] = [*primary_types, *other_types]

    for animal_type in animal_types:
        builder.button(text=animal_type.name, callback_data=f"admin_setanimal_{post_id}_{animal_type.id}")
    builder.button(text=bot_content.button("back"), callback_data=f"admin_back_{post_id}")
    builder.adjust(*_two_column_rows(len(animal_types), footer_count=1))
    return builder.as_markup()


def get_time_slots_kb(target_date: date, free_times: list[time]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for slot_time in free_times:
        builder.button(
            text=slot_time.strftime("%H:%M"),
            callback_data=f"time_{target_date.isoformat()}_{slot_time.strftime('%H:%M')}",
        )
    builder.button(text=bot_content.button("back_to_calendar"), callback_data="schedule_manual")
    builder.adjust(3, 1)
    return builder.as_markup()
