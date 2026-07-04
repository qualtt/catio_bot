from aiogram import Router, F, Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from db.database import async_session
from db.models.post import Post, PostStatus
from db.crud import add_user_score, ensure_animal_type, get_animal_type_name, get_next_auto_slot
from bot.config import config
from bot.content import bot_content
from bot.keyboards.inline import get_admin_animal_change_kb, get_admin_approval_kb

admin_router = Router()


def is_admin(callback: CallbackQuery) -> bool:
    return callback.from_user.id == config.ADMIN_ID


def admin_post_caption(post: Post) -> str:
    if post.user and post.user.username:
        author = f"@{post.user.username}"
    elif post.user:
        author = str(post.user.telegram_id)
    else:
        author = bot_content.message("author_unknown")

    schedule = (
        post.schedule_time.strftime("%Y-%m-%d %H:%M")
        if post.schedule_time
        else bot_content.message("schedule_not_selected")
    )
    return bot_content.message(
        "admin_new_submission_caption",
        animal_type=post.animal_type,
        schedule=schedule,
        author=author,
    )

@admin_router.callback_query(F.data.startswith("admin_approve_"))
async def handle_admin_approve(callback: CallbackQuery, bot: Bot):
    if not is_admin(callback):
        await callback.answer(bot_content.message("not_admin"), show_alert=True)
        return

    post_id = int(callback.data.split("_")[2])
    
    async with async_session() as session:
        stmt = select(Post).options(selectinload(Post.user)).where(Post.id == post_id)
        post = (await session.execute(stmt)).scalar_one_or_none()
        
        if not post or post.status != PostStatus.PENDING:
            await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
            return

        if post.schedule_time is None:
            schedule_time = await get_next_auto_slot(session)
            post.schedule_time = schedule_time
        else:
            schedule_time = post.schedule_time

        # Approve post
        post.status = PostStatus.APPROVED
        await ensure_animal_type(session, post.animal_type)
        await add_user_score(session, post.user_id, 10 if post.is_auto_scheduled else 5)
        await session.commit()

        schedule_text = schedule_time.strftime("%Y-%m-%d %H:%M")
        await callback.message.edit_caption(
            caption=bot_content.message("approved_caption", schedule=schedule_text)
        )
        
        # Notify user
        try:
            await bot.send_message(
                chat_id=post.user.telegram_id,
                text=bot_content.message(
                    "approved_user_notification",
                    animal_type=post.animal_type,
                    schedule=schedule_text,
                )
            )
        except TelegramAPIError:
            pass
        await callback.answer(bot_content.message("approved_callback"))

@admin_router.callback_query(F.data.startswith("admin_reject_"))
async def handle_admin_reject(callback: CallbackQuery, bot: Bot):
    if not is_admin(callback):
        await callback.answer(bot_content.message("not_admin"), show_alert=True)
        return

    post_id = int(callback.data.split("_")[2])
    
    async with async_session() as session:
        stmt = select(Post).options(selectinload(Post.user)).where(Post.id == post_id)
        post = (await session.execute(stmt)).scalar_one_or_none()
        
        if not post or post.status != PostStatus.PENDING:
            await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
            return

        post.status = PostStatus.REJECTED
        await session.commit()

        try:
            await bot.send_message(
                chat_id=post.user.telegram_id,
                text=bot_content.message("rejected_user_notification"),
            )
        except TelegramAPIError:
            pass
            
    await callback.message.edit_caption(caption=bot_content.message("rejected_caption"))
    await callback.answer(bot_content.message("rejected_callback"))


@admin_router.callback_query(F.data.startswith("admin_change_"))
async def handle_admin_change(callback: CallbackQuery):
    if not is_admin(callback):
        await callback.answer(bot_content.message("not_admin"), show_alert=True)
        return

    post_id = int(callback.data.split("_")[2])
    await callback.message.edit_reply_markup(reply_markup=await get_admin_animal_change_kb(post_id))


@admin_router.callback_query(F.data.startswith("admin_back_"))
async def handle_admin_back(callback: CallbackQuery):
    if not is_admin(callback):
        await callback.answer(bot_content.message("not_admin"), show_alert=True)
        return

    post_id = int(callback.data.split("_")[2])
    await callback.message.edit_reply_markup(reply_markup=get_admin_approval_kb(post_id))


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

        stmt = select(Post).options(selectinload(Post.user)).where(Post.id == post_id)
        post = (await session.execute(stmt)).scalar_one_or_none()
        if not post or post.status != PostStatus.PENDING:
            await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
            return

        post.animal_type = animal_type
        await session.commit()
        caption = admin_post_caption(post)

    await callback.answer(bot_content.message("animal_changed"))
    await callback.message.edit_caption(caption=caption, reply_markup=get_admin_approval_kb(post_id))
