import logging
from datetime import date

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, InputMediaPhoto

from bot.keyboards.inline import (
    get_admin_album_kb,
    get_admin_album_view_kb,
    get_admin_approval_kb,
    get_admin_schedule_kb,
)
from bot.services.captions import (
    admin_album_control_text,
    admin_album_view_caption,
)
from db.database import async_session
from db.models.post import Post, PostStatus

from .helpers import *

logger = logging.getLogger(__name__)


async def edit_admin_album_view_message(
    target,
    session,
    post: Post,
    *,
    use_media: bool = True,
) -> None:
    posts = await load_submission_group_posts(session, post)
    caption = admin_album_view_caption(posts, post, author=post_author(post))
    reply_markup = get_admin_album_view_kb(posts, post)

    if use_media:
        await target.edit_media(
            media=InputMediaPhoto(media=post.file_id, caption=caption),
            reply_markup=reply_markup,
        )
        return

    await target.edit_caption(caption=caption, reply_markup=reply_markup)


async def refresh_admin_album_control(callback: CallbackQuery, session, post: Post) -> None:
    posts = await load_submission_group_posts(session, post)
    if callback.message.text:
        await callback.message.edit_text(
            admin_album_control_text(posts, author=post_author(post)),
            reply_markup=get_admin_album_kb(posts),
        )
        return

    await edit_admin_album_view_message(callback.message, session, post, use_media=True)


async def edit_admin_rejection_result(
    bot: Bot,
    session,
    post: Post,
    *,
    chat_id: int,
    message_id: int,
    is_album_control: bool,
    is_album_view: bool = False,
    reason: str | None = None,
) -> None:
    if is_album_control:
        posts = await load_submission_group_posts(session, post)
        if is_album_view:
            await bot.edit_message_media(
                chat_id=chat_id,
                message_id=message_id,
                media=InputMediaPhoto(
                    media=post.file_id,
                    caption=admin_album_view_caption(posts, post, author=post_author(post)),
                ),
                reply_markup=get_admin_album_view_kb(posts, post),
            )
            return

        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=admin_album_control_text(posts, author=post_author(post)),
            reply_markup=get_admin_album_kb(posts),
        )
        return

    await bot.edit_message_caption(
        chat_id=chat_id,
        message_id=message_id,
        caption=rejected_admin_caption(reason),
    )


async def edit_admin_animal_change_result(
    bot: Bot,
    session,
    post: Post,
    *,
    chat_id: int,
    message_id: int,
    is_album_control: bool,
    is_album_view: bool = False,
) -> None:
    if is_album_control:
        posts = await load_submission_group_posts(session, post)
        if is_album_view:
            await bot.edit_message_media(
                chat_id=chat_id,
                message_id=message_id,
                media=InputMediaPhoto(
                    media=post.file_id,
                    caption=admin_album_view_caption(posts, post, author=post_author(post)),
                ),
                reply_markup=get_admin_album_view_kb(posts, post),
            )
            return

        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=admin_album_control_text(posts, author=post_author(post)),
            reply_markup=get_admin_album_kb(posts),
        )
        return

    await bot.edit_message_caption(
        chat_id=chat_id,
        message_id=message_id,
        caption=admin_post_caption(post),
        reply_markup=get_admin_approval_kb(post.id, post.user_id),
    )


async def reject_post(session, post: Post, *, reason: str | None = None) -> None:
    post.status = PostStatus.REJECTED
    await session.commit()


async def notify_rejected_post_user(bot: Bot, post: Post, *, reason: str | None = None) -> None:
    if not post.user:
        return

    try:
        await bot.send_message(
            chat_id=post.user.telegram_id,
            text=rejected_user_notification_text(post, reason=reason),
        )
    except TelegramAPIError:
        pass


async def send_admin_schedule(target, target_date: date, *, callback_text: str | None = None) -> None:
    async with async_session() as session:
        posts = await load_admin_schedule_posts(session, target_date)

    text = admin_schedule_text(target_date, posts)
    reply_markup = get_admin_schedule_kb(target_date, posts)
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=reply_markup)
        await target.answer(callback_text)
        return
    await target.answer(text, reply_markup=reply_markup)


