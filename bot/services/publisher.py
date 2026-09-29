import asyncio
import logging
from datetime import timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BufferedInputFile
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from bot.config import config
from bot.content import bot_content
from bot.handlers.identify import create_and_send_ready_identification_batches
from bot.services.photo_storage import download_photo, restore_photo_from_telegram
from bot.services.tournaments import run_tournament_maintenance
from db.crud import create_channel_history_item, get_next_auto_slot, now_in_app_tz
from db.database import async_session
from db.models.channel_history import ChannelHistory
from db.models.post import Post, PostStatus

logger = logging.getLogger(__name__)


def _channel_history_chat_id() -> int | None:
    try:
        return int(config.CHANNEL_ID)
    except (TypeError, ValueError):
        return None


async def post_photo_input(bot: Bot, post: Post):
    if not post.photo:
        return post.file_id

    photo = post.photo
    try:
        photo_bytes = await download_photo(
            storage_bucket=photo.storage_bucket,
            storage_key=photo.storage_key,
        )
    except Exception:
        # The S3 copy is gone or unreachable: the Telegram copy is still valid, so the
        # post must not get stuck. Put the file back to S3 if possible, else send by file_id.
        logger.exception(
            "Photo %s of post %s is unavailable in S3 (%s), restoring from Telegram",
            photo.id,
            post.id,
            photo.storage_key,
        )
        try:
            photo_bytes = await restore_photo_from_telegram(
                bot,
                file_id=photo.telegram_file_id or post.file_id,
                storage_bucket=photo.storage_bucket,
                storage_key=photo.storage_key,
                expected_sha256=photo.sha256,
                content_type=photo.content_type,
            )
            logger.warning("Restored photo %s of post %s to S3 from Telegram", photo.id, post.id)
        except Exception:
            logger.exception("Failed to restore photo %s from Telegram, sending post %s by file_id", photo.id, post.id)
            return post.file_id

    filename = f"{photo.sha256 or post.id}.jpg"
    return BufferedInputFile(photo_bytes, filename=filename)


async def _verify_and_recover_published_post(bot: Bot, session, post: Post, actual_published_at) -> bool:
    chat_id = _channel_history_chat_id()
    if not chat_id:
        return False

    try:
        max_msg_id = await session.scalar(
            select(func.max(ChannelHistory.message_id)).where(ChannelHistory.chat_id == chat_id)
        )
        if not max_msg_id:
            return False

        target_chat = config.ADMIN_ID or (post.user.telegram_id if post.user else None)
        if not target_chat:
            return False

        for candidate_id in range(max_msg_id + 1, max_msg_id + 6):
            try:
                msg = await bot.forward_message(
                    chat_id=target_chat,
                    from_chat_id=config.CHANNEL_ID,
                    message_id=candidate_id,
                )
                if msg:
                    post.status = PostStatus.PUBLISHED
                    post.message_id = candidate_id
                    await session.commit()
                    try:
                        await create_channel_history_item(
                            session,
                            chat_id=chat_id,
                            message_id=candidate_id,
                            photo_id=post.photo_id,
                            file_id=post.file_id,
                            published_at=actual_published_at,
                            animal_type=post.animal_type,
                        )
                    except Exception:
                        logger.exception("Failed to index recovered post %s in channel_history", post.id)
                    logger.info("Recovered post %s with channel message_id %s after timeout", post.id, candidate_id)
                    return True
            except TelegramAPIError:
                continue
    except Exception:
        logger.exception("Error checking for post recovery in channel")

    return False


async def publish_post(bot: Bot, session, post: Post, *, published_at=None) -> None:
    actual_published_at = published_at or now_in_app_tz()
    try:
        photo = await post_photo_input(bot, post)
        message = await bot.send_photo(
            chat_id=config.CHANNEL_ID,
            photo=photo,
            request_timeout=300,
        )
    except Exception:
        logger.exception("Failed to publish post %s, checking if post was already sent to channel...", post.id)
        await session.rollback()
        recovered = await _verify_and_recover_published_post(bot, session, post, actual_published_at)
        if recovered:
            return
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


async def _defer_failed_post(session, post: Post) -> None:
    # publish_post rolled the session back, which expired the post.
    await session.refresh(post)
    post.publish_attempts += 1

    if post.publish_attempts < config.PUBLISH_MAX_ATTEMPTS:
        post.schedule_time = now_in_app_tz() + timedelta(minutes=5)
        await session.commit()
        return

    # Give up on quick retries: a post that keeps failing must not end up in the
    # channel at a random hour once the cause is fixed, so it goes to a regular slot.
    # Clearing schedule_time first keeps the post from occupying a day it is leaving.
    post.schedule_time = None
    await session.flush()
    post.schedule_time = await get_next_auto_slot(
        session,
        animal_type=post.animal_type,
        start_at=now_in_app_tz() + timedelta(minutes=5),
    )
    post.publish_attempts = 0
    await session.commit()
    logger.error(
        "Post %d failed to publish %d times in a row, moved to the next regular slot %s",
        post.id,
        config.PUBLISH_MAX_ATTEMPTS,
        post.schedule_time.isoformat(),
    )


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
                    Post.schedule_time >= now - timedelta(hours=1),
                )
                .order_by(Post.schedule_time, Post.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            post = (await session.execute(stmt)).scalar_one_or_none()
            if post is None:
                break

            post_id = post.id
            try:
                await publish_post(bot, session, post)
                published_count += 1
            except Exception:
                logger.exception("Failed to publish post %d", post_id)
                try:
                    await _defer_failed_post(session, post)
                except Exception:
                    logger.exception("Failed to defer retry schedule_time for post %d", post_id)
                break

    return published_count


async def publisher_loop(bot: Bot) -> None:
    while True:
        try:
            published_count = await publish_due_posts(bot)
            if published_count:
                logger.info("Published %s scheduled posts", published_count)
            review_batch_count = await create_and_send_ready_identification_batches(bot)
            if review_batch_count:
                logger.info(
                    "Sent %s old-photo identification review batches",
                    review_batch_count,
                )
            await run_tournament_maintenance(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Publisher loop failed")

        await asyncio.sleep(config.PUBLISHER_POLL_INTERVAL_SECONDS)
