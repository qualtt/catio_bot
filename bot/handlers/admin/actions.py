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


async def send_pending_posts_to_admin(bot: Bot, target) -> None:
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    async with async_session() as session:
        stmt = (
            select(Post)
            .options(selectinload(Post.user), selectinload(Post.duplicate_of_photo))
            .where(Post.status == PostStatus.PENDING)
            .order_by(Post.created_at.asc())
        )
        pending_posts = list((await session.execute(stmt)).scalars())

    if not pending_posts:
        if isinstance(target, CallbackQuery):
            await target.answer(bot_content.message("admin_no_pending_posts"), show_alert=True)
        else:
            await target.answer(bot_content.message("admin_no_pending_posts"))
        return

    groups: list[list[Post]] = []
    group_map: dict[str, list[Post]] = {}

    for post in pending_posts:
        if post.submission_group_id:
            if post.submission_group_id not in group_map:
                group_list = []
                group_map[post.submission_group_id] = group_list
                groups.append(group_list)
            group_map[post.submission_group_id].append(post)
        else:
            groups.append([post])

    count_msg = bot_content.message(
        "admin_pending_posts_count",
        posts_count=len(pending_posts),
        submissions_count=len(groups),
    )
    if isinstance(target, CallbackQuery):
        await target.message.answer(count_msg)
        await target.answer()
    else:
        await target.answer(count_msg)

    from bot.handlers.suggest.actions import (
        _send_album_submission_to_admin,
        _send_single_submission_to_admin,
    )

    for group in groups:
        author = post_author(group[0])
        if len(group) == 1 and group[0].submission_group_id is None:
            post = group[0]
            await _send_single_submission_to_admin(
                bot,
                post=post,
                file_id=post.file_id,
                animal_type=post.animal_type,
                schedule_time=post.schedule_time,
                author=author,
            )
        else:
            await _send_album_submission_to_admin(bot, posts=group, author=author)
