import logging
from datetime import datetime, timedelta

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from bot.keyboards.inline import (
    get_admin_approval_kb,
    get_animal_type_kb,
    get_other_animal_type_kb,
    get_schedule_choice_kb,
    get_time_slots_kb,
)
from bot.config import config
from bot.content import bot_content
from db.database import async_session
from db.crud import (
    canonical_animal_type,
    combine_slot,
    create_photo,
    create_post,
    get_animal_type_name,
    get_day_availability,
    get_free_slot_times,
    get_next_auto_slot,
    get_or_create_user,
    get_photo_by_telegram_unique_id,
    now_in_app_tz,
    parse_daily_slot_times,
)
from bot.services.photo_storage import upload_telegram_photo

suggest_router = Router()
logger = logging.getLogger(__name__)


def user_display(user) -> str:
    if user.username:
        return f"@{user.username}"
    return str(user.id)


class SuggestState(StatesGroup):
    waiting_for_animal_type = State()
    waiting_for_custom_animal_type = State()
    waiting_for_schedule_type = State()

@suggest_router.message(F.photo)
async def handle_photo(message: Message, state: FSMContext, bot: Bot):
    photo_size = message.photo[-1]
    file_id = photo_size.file_id
    file_unique_id = photo_size.file_unique_id
    
    async with async_session() as session:
        user = await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name,
        )
        photo = await get_photo_by_telegram_unique_id(session, file_unique_id)

    if photo is None:
        try:
            stored_photo = await upload_telegram_photo(
                bot,
                file_id=file_id,
                file_unique_id=file_unique_id,
                source="submissions",
            )
        except Exception:
            logger.exception("Failed to store submitted photo")
            await message.answer(bot_content.message("photo_storage_failed"))
            return

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
            )

    await state.update_data(file_id=file_id, photo_id=photo.id, user_id=user.id)
    await state.set_state(SuggestState.waiting_for_animal_type)
    
    await message.reply(bot_content.message("ask_animal_type"), reply_markup=await get_animal_type_kb())


async def ask_for_schedule(message: Message, state: FSMContext, animal_type: str):
    await state.update_data(animal_type=animal_type)
    await state.set_state(SuggestState.waiting_for_schedule_type)
    await message.answer(
        bot_content.message("animal_type_selected", animal_type=animal_type),
        reply_markup=get_schedule_choice_kb(),
    )


async def select_animal_type(callback: CallbackQuery, state: FSMContext, animal_type: str):
    await state.update_data(animal_type=animal_type)
    await state.set_state(SuggestState.waiting_for_schedule_type)
    await callback.message.edit_text(
        bot_content.message("animal_type_selected", animal_type=animal_type),
        reply_markup=get_schedule_choice_kb(),
    )
    await callback.answer()


@suggest_router.callback_query(SuggestState.waiting_for_animal_type, F.data == "animal_other")
async def handle_other_animal_type(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        bot_content.message("choose_other_animal_type"),
        reply_markup=await get_other_animal_type_kb(),
    )
    await callback.answer()


@suggest_router.callback_query(SuggestState.waiting_for_animal_type, F.data == "animal_back")
async def handle_animal_type_back(callback: CallbackQuery):
    await callback.message.edit_text(
        bot_content.message("ask_animal_type"),
        reply_markup=await get_animal_type_kb(),
    )
    await callback.answer()


@suggest_router.callback_query(SuggestState.waiting_for_animal_type, F.data == "animal_custom")
async def handle_custom_animal_type_button(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SuggestState.waiting_for_custom_animal_type)
    await callback.message.edit_text(bot_content.message("ask_custom_animal_type"))
    await callback.answer()


@suggest_router.callback_query(SuggestState.waiting_for_animal_type, F.data.startswith("animal_id_"))
async def handle_animal_type(callback: CallbackQuery, state: FSMContext):
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
    await select_animal_type(callback, state, animal_type)


@suggest_router.callback_query(SuggestState.waiting_for_animal_type, F.data.startswith("animal_extra_id_"))
async def handle_extra_animal_type(callback: CallbackQuery, state: FSMContext):
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
    await select_animal_type(callback, state, animal_type)


@suggest_router.message(SuggestState.waiting_for_custom_animal_type)
async def handle_custom_animal_type(message: Message, state: FSMContext):
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

    await ask_for_schedule(message, state, animal_type)

@suggest_router.callback_query(SuggestState.waiting_for_schedule_type, F.data == "schedule_auto")
async def handle_schedule_auto(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    file_id = data.get("file_id")
    photo_id = data.get("photo_id")
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
        )
        
    await state.clear()
    await callback.message.edit_text(
        bot_content.message(
            "photo_submitted_auto",
            schedule=schedule_time.strftime("%Y-%m-%d %H:%M"),
        )
    )
    
    await bot.send_photo(
        chat_id=config.ADMIN_ID,
        photo=file_id,
        caption=bot_content.message(
            "admin_new_submission_caption",
            animal_type=animal_type,
            schedule=schedule_time.strftime("%Y-%m-%d %H:%M"),
            author=user_display(callback.from_user),
        ),
        reply_markup=get_admin_approval_kb(post.id)
    )


@suggest_router.callback_query(SuggestState.waiting_for_schedule_type, F.data == "schedule_manual")
async def handle_schedule_manual(callback: CallbackQuery):
    from bot.keyboards.calendar import build_month_calendar

    today = now_in_app_tz().date()
    min_date = today + timedelta(days=1)
    max_date = min_date + timedelta(days=config.AUTO_POST_DAYS_AHEAD - 1)

    async with async_session() as session:
        availability = await get_day_availability(session, start_date=min_date, days=config.AUTO_POST_DAYS_AHEAD)

    await callback.message.edit_text(
        bot_content.message("choose_publication_date"),
        reply_markup=build_month_calendar(
            year=min_date.year,
            month=min_date.month,
            availability=availability,
            min_date=min_date,
            max_date=max_date,
            max_slots=len(parse_daily_slot_times()),
        ),
    )


@suggest_router.callback_query(SuggestState.waiting_for_schedule_type, F.data.startswith("cal_nav_"))
async def handle_calendar_nav(callback: CallbackQuery):
    from bot.keyboards.calendar import build_month_calendar

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

    async with async_session() as session:
        availability = await get_day_availability(session, start_date=min_date, days=config.AUTO_POST_DAYS_AHEAD)

    await callback.message.edit_reply_markup(
        reply_markup=build_month_calendar(
            year=year,
            month=month,
            availability=availability,
            min_date=min_date,
            max_date=max_date,
            max_slots=len(parse_daily_slot_times()),
        )
    )


@suggest_router.callback_query(SuggestState.waiting_for_schedule_type, F.data.startswith("cal_day_"))
async def handle_calendar_day(callback: CallbackQuery):
    _, _, day_raw = callback.data.split("_")
    target_date = datetime.strptime(day_raw, "%Y-%m-%d").date()

    async with async_session() as session:
        free_times = await get_free_slot_times(session, target_date)

    if not free_times:
        await callback.answer(bot_content.message("no_free_slots"), show_alert=True)
        return

    await callback.message.edit_text(
        bot_content.message("choose_publication_time", date=target_date.strftime("%Y-%m-%d")),
        reply_markup=get_time_slots_kb(target_date, free_times),
    )


@suggest_router.callback_query(SuggestState.waiting_for_schedule_type, F.data.startswith("time_"))
async def handle_manual_time(callback: CallbackQuery, state: FSMContext, bot: Bot):
    _, day_raw, time_raw = callback.data.split("_")
    target_date = datetime.strptime(day_raw, "%Y-%m-%d").date()
    slot_hour, slot_minute = [int(part) for part in time_raw.split(":", 1)]
    schedule_time = combine_slot(target_date, datetime.min.time().replace(hour=slot_hour, minute=slot_minute))

    data = await state.get_data()
    file_id = data.get("file_id")
    photo_id = data.get("photo_id")
    animal_type = data.get("animal_type")
    user_id = data.get("user_id")

    async with async_session() as session:
        free_times = await get_free_slot_times(session, target_date)
        if schedule_time.timetz().replace(tzinfo=None) not in free_times:
            await callback.answer(bot_content.message("slot_taken"), show_alert=True)
            return

        post = await create_post(
            session,
            user_id=user_id,
            file_id=file_id,
            animal_type=animal_type,
            is_auto_scheduled=False,
            manual_time=schedule_time,
            photo_id=photo_id,
        )

    await state.clear()
    await callback.message.edit_text(
        bot_content.message(
            "photo_submitted_manual",
            schedule=schedule_time.strftime("%Y-%m-%d %H:%M"),
        )
    )

    await bot.send_photo(
        chat_id=config.ADMIN_ID,
        photo=file_id,
        caption=bot_content.message(
            "admin_new_submission_caption",
            animal_type=animal_type,
            schedule=schedule_time.strftime("%Y-%m-%d %H:%M"),
            author=user_display(callback.from_user),
        ),
        reply_markup=get_admin_approval_kb(post.id),
    )


@suggest_router.callback_query(SuggestState.waiting_for_schedule_type, F.data == "noop")
async def handle_noop(callback: CallbackQuery):
    await callback.answer()
