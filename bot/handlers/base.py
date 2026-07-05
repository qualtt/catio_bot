import logging
import re

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import CommandStart, Command
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, Message, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext
from bot.config import config
from bot.content import bot_content
from bot.keyboards.inline import get_main_menu_kb
from bot.services.photo_storage import download_photo
from db.database import async_session
from db.crud import (
    ensure_app_timezone,
    get_or_create_user,
    get_photo_by_id,
    get_post_by_id,
    get_random_public_photo,
    get_recent_user_posts,
    get_top_users,
    get_user_post_stats,
    user_can_view_photo,
)
from db.models.photo import Photo
from db.models.post import Post, PostStatus

base_router = Router()
logger = logging.getLogger(__name__)
PHOTO_COMMAND_PATTERN = re.compile(r"^/photo_(\d+)(?:@\w+)?(?:\s|$)")
POST_COMMAND_PATTERN = re.compile(r"^/post_(\d+)(?:@\w+)?(?:\s|$)")


async def answer_with_legacy_reply_keyboard_removed(
    message: Message,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
    **kwargs,
) -> Message:
    sent = await message.answer(
        text,
        reply_markup=ReplyKeyboardRemove(),
        **kwargs,
    )
    if reply_markup is None:
        return sent

    try:
        await sent.edit_reply_markup(reply_markup=reply_markup)
    except TelegramAPIError:
        logger.exception("Failed to attach inline keyboard after removing reply keyboard")
    return sent


def _command_id(text: str | None, pattern: re.Pattern[str]) -> int | None:
    match = pattern.match(text or "")
    if match is None:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _is_admin(message: Message) -> bool:
    return message.from_user.id == config.ADMIN_ID


async def _photo_input(photo: Photo):
    if photo.telegram_file_id:
        return photo.telegram_file_id

    photo_bytes = await download_photo(
        storage_bucket=photo.storage_bucket,
        storage_key=photo.storage_key,
    )
    filename = f"{photo.sha256 or photo.id}.jpg"
    return BufferedInputFile(photo_bytes, filename=filename)


async def _post_photo_input(post: Post):
    if post.photo:
        return await _photo_input(post.photo)
    return post.file_id


def _format_schedule(post: Post) -> str:
    if post.schedule_time is None:
        return bot_content.message("schedule_not_selected")
    return ensure_app_timezone(post.schedule_time).strftime("%Y-%m-%d %H:%M")


async def _send_photo_view(message: Message, bot: Bot, photo: Photo, *, caption: str) -> None:
    try:
        await bot.send_photo(
            chat_id=message.chat.id,
            photo=await _photo_input(photo),
            caption=caption,
            reply_markup=ReplyKeyboardRemove(),
        )
    except Exception:
        logger.exception("Failed to send photo %s", photo.id)
        await message.answer(bot_content.message("photo_view_send_failed"), reply_markup=ReplyKeyboardRemove())


@base_router.message(CommandStart())
async def start_handler(message: Message):
    async with async_session() as session:
        await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name
        )
    
    await answer_with_legacy_reply_keyboard_removed(
        message,
        bot_content.message("start"),
        reply_markup=get_main_menu_kb(),
        parse_mode="HTML",
    )

@base_router.message(Command("help"))
async def help_handler(message: Message):
    await answer_with_legacy_reply_keyboard_removed(
        message,
        bot_content.message("help"),
        reply_markup=get_main_menu_kb(),
    )


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
            ensure_app_timezone(post.schedule_time).strftime("%Y-%m-%d %H:%M")
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


@base_router.message(F.text.regexp(PHOTO_COMMAND_PATTERN))
async def photo_view_handler(message: Message, bot: Bot):
    photo_id = _command_id(message.text, PHOTO_COMMAND_PATTERN)
    if photo_id is None:
        await message.answer(bot_content.message("photo_view_not_found"), reply_markup=ReplyKeyboardRemove())
        return

    async with async_session() as session:
        photo = await get_photo_by_id(session, photo_id)
        allowed = bool(photo) and await user_can_view_photo(
            session,
            photo_id=photo_id,
            telegram_id=message.from_user.id,
            is_admin=_is_admin(message),
        )

    if not photo or not allowed:
        await message.answer(bot_content.message("photo_view_not_found"), reply_markup=ReplyKeyboardRemove())
        return

    await _send_photo_view(
        message,
        bot,
        photo,
        caption=bot_content.message("photo_view_caption", photo_id=photo.id),
    )


@base_router.message(F.text.regexp(POST_COMMAND_PATTERN))
async def post_view_handler(message: Message, bot: Bot):
    post_id = _command_id(message.text, POST_COMMAND_PATTERN)
    if post_id is None:
        await message.answer(bot_content.message("post_view_not_found"), reply_markup=ReplyKeyboardRemove())
        return

    async with async_session() as session:
        post = await get_post_by_id(session, post_id)

    if not post or (not _is_admin(message) and (post.user is None or post.user.telegram_id != message.from_user.id)):
        await message.answer(bot_content.message("post_view_not_found"), reply_markup=ReplyKeyboardRemove())
        return

    try:
        await bot.send_photo(
            chat_id=message.chat.id,
            photo=await _post_photo_input(post),
            caption=bot_content.message(
                "post_view_caption",
                post_id=post.id,
                animal_type=post.animal_type,
                status=bot_content.status_label(post.status),
                schedule=_format_schedule(post),
            ),
            reply_markup=ReplyKeyboardRemove(),
        )
    except Exception:
        logger.exception("Failed to send post %s", post.id)
        await message.answer(bot_content.message("photo_view_send_failed"), reply_markup=ReplyKeyboardRemove())


@base_router.message(Command("random_photo"))
async def random_photo_handler(message: Message, bot: Bot):
    async with async_session() as session:
        photo = await get_random_public_photo(session)

    if not photo:
        await message.answer(bot_content.message("random_photo_not_found"), reply_markup=ReplyKeyboardRemove())
        return

    await _send_photo_view(
        message,
        bot,
        photo,
        caption=bot_content.message("random_photo_caption", photo_id=photo.id),
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
