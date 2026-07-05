from aiogram import Router
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext
from bot.content import bot_content
from bot.keyboards.inline import get_main_menu_kb
from db.database import async_session
from db.crud import get_or_create_user, get_recent_user_posts, get_top_users, get_user_post_stats
from db.models.post import PostStatus

base_router = Router()


async def remove_legacy_reply_keyboard(message: Message) -> None:
    await message.answer(
        bot_content.message("reply_keyboard_removed"),
        reply_markup=ReplyKeyboardRemove(),
    )

@base_router.message(CommandStart())
async def start_handler(message: Message):
    async with async_session() as session:
        await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name
        )
    
    await remove_legacy_reply_keyboard(message)
    await message.answer(bot_content.message("start"), reply_markup=get_main_menu_kb())

@base_router.message(Command("help"))
async def help_handler(message: Message):
    await remove_legacy_reply_keyboard(message)
    await message.answer(bot_content.message("help"), reply_markup=get_main_menu_kb())


@base_router.message(Command("profile"))
async def profile_handler(message: Message):
    async with async_session() as session:
        user = await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name,
        )
        stats = await get_user_post_stats(session, user.id)

    stats_text = "\n".join(
        bot_content.message(
            "status_count_line",
            status=bot_content.status_label(status),
            count=stats.get(status, 0),
        )
        for status in PostStatus
    )
    await message.answer(
        bot_content.message("profile", score=user.score, stats=stats_text),
        reply_markup=ReplyKeyboardRemove(),
    )


@base_router.message(Command("my_posts"))
async def my_posts_handler(message: Message):
    async with async_session() as session:
        user = await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name,
        )
        posts = await get_recent_user_posts(session, user.id)

    if not posts:
        await message.answer(bot_content.message("my_posts_empty"), reply_markup=ReplyKeyboardRemove())
        return

    lines = []
    for post in posts:
        schedule = (
            post.schedule_time.strftime("%Y-%m-%d %H:%M")
            if post.schedule_time
            else bot_content.message("schedule_not_selected")
        )
        lines.append(
            bot_content.message(
                "my_posts_line",
                post_id=post.id,
                animal_type=post.animal_type,
                status=bot_content.status_label(post.status),
                schedule=schedule,
            )
        )

    await message.answer(
        bot_content.message("my_posts_header", posts="\n".join(lines)),
        reply_markup=ReplyKeyboardRemove(),
    )


@base_router.message(Command("top"))
async def top_handler(message: Message):
    async with async_session() as session:
        users = await get_top_users(session)

    if not users:
        await message.answer(bot_content.message("top_empty"), reply_markup=ReplyKeyboardRemove())
        return

    lines = []
    for index, user in enumerate(users, start=1):
        name = user.username or user.full_name or str(user.telegram_id)
        lines.append(bot_content.message("top_line", position=index, name=name, score=user.score))

    await message.answer(
        bot_content.message("top_header", users="\n".join(lines)),
        reply_markup=ReplyKeyboardRemove(),
    )


@base_router.message(Command("cancel"))
async def cancel_handler(message: Message, state: FSMContext):
    current_state = await state.get_state()
    await state.clear()
    message_key = "cancelled" if current_state else "nothing_to_cancel"
    await message.answer(bot_content.message(message_key), reply_markup=ReplyKeyboardRemove())
