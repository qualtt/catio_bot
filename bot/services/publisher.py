import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BufferedInputFile
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from bot.config import config
from bot.content import bot_content
from bot.handlers.identify import create_and_send_ready_identification_batches
from bot.services.photo_storage import download_photo
from bot.services.tournaments import run_tournament_maintenance
from db.crud import create_channel_history_item, now_in_app_tz
from db.database import async_session
from db.models.post import Post, PostStatus

logger = logging.getLogger(__name__)


def _channel_history_chat_id() -> int | None:
    try:
        return int(config.CHANNEL_ID)
    except (TypeError, ValueError):
        return None


async def post_photo_input(post: Post):
    if post.photo:
        photo_bytes = await download_photo(
            storage_bucket=post.photo.storage_bucket,
            storage_key=post.photo.storage_key,
        )
        filename = f"{post.photo.sha256 or post.id}.jpg"
        return BufferedInputFile(photo_bytes, filename=filename)

    return post.file_id


async def publish_post(bot: Bot, session, post: Post, *, published_at=None) -> None:
    actual_published_at = published_at or now_in_app_tz()
    try:
        photo = await post_photo_input(post)
        message = await bot.send_photo(
            chat_id=config.CHANNEL_ID,
            photo=photo,
        )
    except Exception:
        logger.exception("Failed to publish post %s", post.id)
        await session.rollback()
        raise

    post.status = PostStatus.PUBLISHED
    post.message_id = message.message_id
    if published_at is not None:
        post.schedule_time = actual_published_at
    await session.commit()

    try:
        await create_channel_history_item(
            session,
            chat_id=_channel_history_chat_id(),
            message_id=message.message_id,
            photo_id=post.photo_id,
            file_id=post.file_id,
            published_at=actual_published_at,
            animal_type=post.animal_type,
        )
    except Exception:
        logger.exception("Failed to index published post %s in channel_history", post.id)

    if post.user:
        try:
            await bot.send_message(
                chat_id=post.user.telegram_id,
                text=bot_content.message(
                    "published_user_notification",
                    animal_type=post.animal_type,
                ),
            )
        except TelegramAPIError:
            logger.exception("Failed to notify user for post %s", post.id)


async def publish_due_posts(bot: Bot) -> int:
    now = now_in_app_tz()
    published_count = 0

    async with async_session() as session:
        while True:
            stmt = (
                select(Post)
                .options(selectinload(Post.user), selectinload(Post.photo))
                .where(
                    Post.status == PostStatus.APPROVED,
                    Post.schedule_time <= now,
                )
                .order_by(Post.schedule_time, Post.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            post = (await session.execute(stmt)).scalar_one_or_none()
            if post is None:
                break

            try:
                await publish_post(bot, session, post)
            except Exception:
                break

            published_count += 1

    return published_count


async def publisher_loop(bot: Bot) -> None:
    while True:
        try:
            published_count = await publish_due_posts(bot)
            if published_count:
                logger.info("Published %s scheduled posts", published_count)
            review_batch_count = await create_and_send_ready_identification_batches(bot, min_size=1)
            if review_batch_count:
                logger.info("Sent %s old-photo identification review batches", review_batch_count)
            await run_tournament_maintenance(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Publisher loop failed")

        await asyncio.sleep(config.PUBLISHER_POLL_INTERVAL_SECONDS)
