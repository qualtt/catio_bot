import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError

from bot.config import config
from bot.services.photo_storage import delete_photos_batch
from db.crud.photos import delete_photos, get_abandoned_photos
from db.crud.time_utils import now_in_app_tz
from db.database import async_session

logger = logging.getLogger(__name__)


async def _delete_abandoned_photo_row(photo_id: int, older_than: datetime) -> tuple[str, str] | None:
    """Delete one abandoned photo from the DB and return its S3 location.

    The row is locked and re-checked in its own transaction, so a post created after
    the candidate scan either keeps the photo or makes the delete fail on the FK.
    The S3 object must only be removed after this commit succeeds.
    """
    async with async_session() as session:
        photos = await get_abandoned_photos(session, older_than, photo_ids=[photo_id], for_update=True)
        if not photos:
            return None

        photo = photos[0]
        location = (photo.storage_bucket, photo.storage_key)
        try:
            await delete_photos(session, [photo.id])
        except IntegrityError:
            await session.rollback()
            logger.warning("Photo %s became referenced during cleanup, keeping it", photo_id)
            return None

    return location


async def cleanup_abandoned_photos() -> int:
    older_than = now_in_app_tz() - timedelta(hours=config.CLEANUP_EXPIRE_HOURS)

    async with async_session() as session:
        candidate_ids = [photo.id for photo in await get_abandoned_photos(session, older_than)]
    if not candidate_ids:
        return 0

    logger.info("Found %d abandoned photos. Deleting...", len(candidate_ids))

    buckets: dict[str, list[str]] = {}
    for photo_id in candidate_ids:
        location = await _delete_abandoned_photo_row(photo_id, older_than)
        if location is None:
            continue
        bucket, key = location
        logger.info("Deleted abandoned photo %s from DB, removing s3://%s/%s", photo_id, bucket, key)
        buckets.setdefault(bucket, []).append(key)

    for bucket, keys in buckets.items():
        await delete_photos_batch(storage_bucket=bucket, storage_keys=keys)

    deleted_count = sum(len(keys) for keys in buckets.values())
    logger.info("Deleted %d abandoned photos", deleted_count)
    return deleted_count


async def cleanup_loop() -> None:
    logger.info("Cleanup task started")
    while True:
        try:
            await asyncio.sleep(config.CLEANUP_INTERVAL_SECONDS)
            await cleanup_abandoned_photos()
        except asyncio.CancelledError:
            logger.info("Cleanup task cancelled")
            raise
        except Exception:
            logger.exception("Error in cleanup task")
