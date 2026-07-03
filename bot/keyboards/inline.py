from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

ANIMAL_TYPES = ["Кот", "Собака", "Енот", "Лиса", "Другое"]

def get_animal_type_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for animal in ANIMAL_TYPES:
        builder.button(text=animal, callback_data=f"animal_{animal}")
    builder.adjust(2)
    return builder.as_markup()

def get_schedule_choice_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Авторазмещение (+10 баллов)", callback_data="schedule_auto")
    # For now we can keep manual selection simple or add a datepicker later
    # builder.button(text="Выбрать день вручную", callback_data="schedule_manual")
    builder.adjust(1)
    return builder.as_markup()

def get_admin_approval_kb(post_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Одобрить", callback_data=f"admin_approve_{post_id}")
    builder.button(text="❌ Отклонить", callback_data=f"admin_reject_{post_id}")
    builder.button(text="✏️ Изменить животное", callback_data=f"admin_change_{post_id}")
    builder.adjust(2, 1)
    return builder.as_markup()
