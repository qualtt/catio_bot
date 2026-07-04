import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BufferedInputFile
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from bot.config import config
from bot.content import bot_content
from bot.services.photo_storage import download_photo
from db.crud import now_in_app_tz
from db.database import async_session
from db.models.post import Post, PostStatus

logger = logging.getLogger(__name__)


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
                photo = post.file_id
                if post.photo:
                    photo_bytes = await download_photo(
                        storage_bucket=post.photo.storage_bucket,
                        storage_key=post.photo.storage_key,
                    )
                    filename = f"{post.photo.sha256 or post.id}.jpg"
                    photo = BufferedInputFile(photo_bytes, filename=filename)

                message = await bot.send_photo(
                    chat_id=config.CHANNEL_ID,
                    photo=photo,
                    caption=bot_content.message("channel_post_caption", animal_type=post.animal_type),
                )
            except TelegramAPIError:
                logger.exception("Failed to publish post %s", post.id)
                await session.rollback()
                break

            post.status = PostStatus.PUBLISHED
            post.message_id = message.message_id
            await session.commit()
            published_count += 1

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

    return published_count


async def publisher_loop(bot: Bot) -> None:
    while True:
        try:
            published_count = await publish_due_posts(bot)
            if published_count:
                logger.info("Published %s scheduled posts", published_count)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Publisher loop failed")

        await asyncio.sleep(config.PUBLISHER_POLL_INTERVAL_SECONDS)
