from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from bot.config import config
from bot.content import bot_content
from bot.keyboards.inline import get_admin_album_kb, get_admin_animal_change_kb, get_admin_approval_kb
from bot.services.captions import admin_album_control_text, submission_caption
from bot.services.scoring import award_post_approval_score
from db.crud import ensure_animal_type, get_animal_type_name, get_next_auto_slot
from db.database import async_session
from db.models.post import Post, PostStatus

admin_router = Router()


def is_admin(callback: CallbackQuery) -> bool:
    return callback.from_user.id == config.ADMIN_ID


def post_author(post: Post) -> str:
    if post.user and post.user.username:
        return f"@{post.user.username}"
    if post.user:
        return str(post.user.telegram_id)
    return bot_content.message("author_unknown")


def admin_post_caption(post: Post) -> str:
    schedule = (
        post.schedule_time.strftime("%Y-%m-%d %H:%M")
        if post.schedule_time
        else bot_content.message("schedule_not_selected")
    )
    return submission_caption(
        animal_type=post.animal_type,
        schedule=schedule,
        author=post_author(post),
        duplicate_of_photo_id=post.duplicate_of_photo_id,
        duplicate_distance=post.duplicate_distance,
    )


async def load_post(session, post_id: int) -> Post | None:
    stmt = select(Post).options(selectinload(Post.user)).where(Post.id == post_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def load_submission_group_posts(session, post: Post) -> list[Post]:
    if not post.submission_group_id:
        return [post]

    stmt = (
        select(Post)
        .options(selectinload(Post.user))
        .where(Post.submission_group_id == post.submission_group_id)
        .order_by(Post.submission_group_index.asc(), Post.id.asc())
    )
    return list((await session.execute(stmt)).scalars())


async def refresh_admin_album_control(callback: CallbackQuery, session, post: Post) -> None:
    posts = await load_submission_group_posts(session, post)
    await callback.message.edit_text(
        admin_album_control_text(posts, author=post_author(post)),
        reply_markup=get_admin_album_kb(posts),
    )


def callback_is_album_control(callback: CallbackQuery, post: Post) -> bool:
    return bool(post.submission_group_id and callback.message and callback.message.text)


@admin_router.callback_query(F.data.startswith("admin_approve_"))
async def handle_admin_approve(callback: CallbackQuery, bot: Bot):
    if not is_admin(callback):
        await callback.answer(bot_content.message("not_admin"), show_alert=True)
        return

    post_id = int(callback.data.split("_")[2])

    async with async_session() as session:
        post = await load_post(session, post_id)

        if not post or post.status != PostStatus.PENDING:
            await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
            return

        if post.schedule_time is None:
            schedule_time = await get_next_auto_slot(session)
            post.schedule_time = schedule_time
        else:
            schedule_time = post.schedule_time

        post.status = PostStatus.APPROVED
        await ensure_animal_type(session, post.animal_type)
        score_award = await award_post_approval_score(session, post)
        await session.commit()

        schedule_text = schedule_time.strftime("%Y-%m-%d %H:%M")
        if callback_is_album_control(callback, post):
            await refresh_admin_album_control(callback, session, post)
        else:
            await callback.message.edit_caption(
                caption=bot_content.message(
                    "approved_caption",
                    schedule=schedule_text,
                    points=score_award.points,
                )
            )

        try:
            await bot.send_message(
                chat_id=post.user.telegram_id,
                text=bot_content.message(
                    "approved_user_notification",
                    animal_type=post.animal_type,
                    schedule=schedule_text,
                    points=score_award.points,
                )
            )
        except TelegramAPIError:
            pass
        await callback.answer(bot_content.message("approved_callback", points=score_award.points))


@admin_router.callback_query(F.data.startswith("admin_reject_"))
async def handle_admin_reject(callback: CallbackQuery, bot: Bot):
    if not is_admin(callback):
        await callback.answer(bot_content.message("not_admin"), show_alert=True)
        return

    post_id = int(callback.data.split("_")[2])

    async with async_session() as session:
        post = await load_post(session, post_id)

        if not post or post.status != PostStatus.PENDING:
            await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
            return

        post.status = PostStatus.REJECTED
        await session.commit()

        if callback_is_album_control(callback, post):
            await refresh_admin_album_control(callback, session, post)
        else:
            await callback.message.edit_caption(caption=bot_content.message("rejected_caption"))

        try:
            await bot.send_message(
                chat_id=post.user.telegram_id,
                text=bot_content.message("rejected_user_notification"),
            )
        except TelegramAPIError:
            pass

    await callback.answer(bot_content.message("rejected_callback"))


@admin_router.callback_query(F.data.startswith("admin_change_"))
async def handle_admin_change(callback: CallbackQuery):
    if not is_admin(callback):
        await callback.answer(bot_content.message("not_admin"), show_alert=True)
        return

    post_id = int(callback.data.split("_")[2])
    async with async_session() as session:
        post = await load_post(session, post_id)
        if not post or post.status != PostStatus.PENDING:
            await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
            return

    await callback.message.edit_reply_markup(reply_markup=await get_admin_animal_change_kb(post_id))
    await callback.answer()


@admin_router.callback_query(F.data.startswith("admin_back_"))
async def handle_admin_back(callback: CallbackQuery):
    if not is_admin(callback):
        await callback.answer(bot_content.message("not_admin"), show_alert=True)
        return

    post_id = int(callback.data.split("_")[2])
    async with async_session() as session:
        post = await load_post(session, post_id)
        if not post:
            await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
            return

        if callback_is_album_control(callback, post):
            await refresh_admin_album_control(callback, session, post)
        else:
            await callback.message.edit_reply_markup(reply_markup=get_admin_approval_kb(post_id))

    await callback.answer()


@admin_router.callback_query(F.data.startswith("admin_setanimal_"))
async def handle_admin_set_animal(callback: CallbackQuery):
    if not is_admin(callback):
        await callback.answer(bot_content.message("not_admin"), show_alert=True)
        return

    _, _, post_id_raw, animal_type_id_raw = callback.data.split("_", 3)
    post_id = int(post_id_raw)
    try:
        animal_type_id = int(animal_type_id_raw)
    except ValueError:
        await callback.answer(bot_content.message("animal_type_not_found"), show_alert=True)
        return

    async with async_session() as session:
        animal_type = await get_animal_type_name(session, animal_type_id)
        if not animal_type:
            await callback.answer(bot_content.message("animal_type_not_found"), show_alert=True)
            return

        post = await load_post(session, post_id)
        if not post or post.status != PostStatus.PENDING:
            await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
            return

        post.animal_type = animal_type
        await session.commit()

        if callback_is_album_control(callback, post):
            await refresh_admin_album_control(callback, session, post)
        else:
            await callback.message.edit_caption(
                caption=admin_post_caption(post),
                reply_markup=get_admin_approval_kb(post_id),
            )

    await callback.answer(bot_content.message("animal_changed"))
