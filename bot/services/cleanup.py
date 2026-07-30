import asyncio
import logging
from datetime import datetime, timedelta

from bot.config import config
from bot.services.photo_storage import delete_photos_batch
from db.crud.photos import get_abandoned_photos, delete_photos
from db.database import async_session

logger = logging.getLogger(__name__)

async def cleanup_loop() -> None:
    logger.info("Cleanup task started")
    while True:
        try:
            await asyncio.sleep(config.CLEANUP_INTERVAL_SECONDS)
            older_than = datetime.utcnow() - timedelta(hours=config.CLEANUP_EXPIRE_HOURS)
            
            async with async_session() as session:
                abandoned_photos = await get_abandoned_photos(session, older_than)
                if not abandoned_photos:
                    continue
                
                logger.info(f"Found {len(abandoned_photos)} abandoned photos. Deleting...")
                
                # Group by bucket
                buckets = {}
                for photo in abandoned_photos:
                    if photo.storage_bucket not in buckets:
                        buckets[photo.storage_bucket] = []
                    buckets[photo.storage_bucket].append(photo.storage_key)
                
                # Delete from S3
                for bucket, keys in buckets.items():
                    await delete_photos_batch(storage_bucket=bucket, storage_keys=keys)
                    
                # Delete from DB
                photo_ids = [photo.id for photo in abandoned_photos]
                await delete_photos(session, photo_ids)
                
                logger.info(f"Deleted {len(abandoned_photos)} abandoned photos")
        except asyncio.CancelledError:
            logger.info("Cleanup task cancelled")
            raise
        except Exception:
            logger.exception("Error in cleanup task")
