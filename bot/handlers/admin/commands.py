import logging

from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.config import config
from bot.content import bot_content
from bot.keyboards.inline import (
    get_admin_menu_kb,
)
from db.crud import (
    now_in_app_tz,
)
from db.database import async_session

from .actions import *
from .helpers import *
from .router import AdminState, admin_router

logger = logging.getLogger(__name__)


@admin_router.message(Command("admin"))
async def admin_menu_handler(message: Message):
    if message.from_user.id != config.ADMIN_ID:
        await message.answer(bot_content.message("not_admin"))
        return
    await message.answer(bot_content.message("admin_menu"), reply_markup=get_admin_menu_kb())


@admin_router.message(Command("schedule"))
async def admin_schedule_command(message: Message):
    if message.from_user.id != config.ADMIN_ID:
        await message.answer(bot_content.message("not_admin"))
        return
    await send_admin_schedule(message, now_in_app_tz().date())


@admin_router.message(Command("stats"))
async def admin_stats_command(message: Message):
    if not is_admin_user(message.from_user.id):
        await message.answer(bot_content.message("not_admin"))
        return
    async with async_session() as session:
        text = await load_admin_stats(session)
    await message.answer(text, reply_markup=get_admin_menu_kb())


@admin_router.message(Command("pending", "moderation"))
async def admin_pending_command(message: Message, bot: Bot):
    if not is_admin_user(message.from_user.id):
        await message.answer(bot_content.message("not_admin"))
        return
    await send_pending_posts_to_admin(bot, message)


@admin_router.message(Command("muted"))
async def admin_muted_command(message: Message):
    if not is_admin_user(message.from_user.id):
        await message.answer(bot_content.message("not_admin"))
        return
    async with async_session() as session:
        from db.crud import get_muted_users

        users = await get_muted_users(session)

    if not users:
        await message.answer("Нет замученных пользователей.")
        return

    from bot.keyboards.inline import get_muted_users_kb

    await message.answer("Список замученных пользователей:", reply_markup=get_muted_users_kb(users))


async def _start_broadcast_prompt(target: Message, state: FSMContext) -> None:
    await state.set_state(AdminState.waiting_for_broadcast_text)
    await state.set_data({})
    await target.answer(
        bot_content.message("admin_broadcast_prompt"),
        reply_markup=get_admin_menu_kb(),
    )


@admin_router.message(Command("broadcast"))
async def admin_broadcast_command(message: Message, state: FSMContext):
    if not is_admin_user(message.from_user.id):
        await message.answer(bot_content.message("not_admin"))
        return
    await _start_broadcast_prompt(message, state)
