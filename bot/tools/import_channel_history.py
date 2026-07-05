from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import BinaryIO

from pyrogram import Client
from pyrogram.types import Message

from bot.config import config
from bot.services.photo_storage import photo_metadata_from_bytes, upload_photo_bytes
from db.crud import (
    create_channel_history_item,
    create_photo,
    get_channel_history_item,
    get_photo_by_telegram_unique_id,
    update_photo_metadata,
)
from db.database import async_session
from db.models.photo import Photo


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ImportResult:
    imported: bool
    uploaded: bool = False
    reused: bool = False
    backfilled: bool = False
    already_indexed: bool = False


@dataclass
class ImportStats:
    scanned: int = 0
    photos: int = 0
    imported: int = 0
    uploaded: int = 0
    reused: int = 0
    backfilled: int = 0
    already_indexed: int = 0
    failed: int = 0

    def record(self, result: ImportResult) -> None:
        if not result.imported:
            return
        self.imported += 1
        self.uploaded += int(result.uploaded)
        self.reused += int(result.reused)
        self.backfilled += int(result.backfilled)
        self.already_indexed += int(result.already_indexed)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import channel photo history into S3 and database.")
    parser.add_argument("--channel", default=config.CHANNEL_ID, help="Channel username/id to import from.")
    parser.add_argument("--limit", type=int, default=0, help="Max messages to scan, 0 means all history.")
    parser.add_argument("--dry-run", action="store_true", help="Scan history without S3/DB writes.")
    return parser.parse_args()


def require_userbot_config() -> None:
    if not config.API_ID or not config.API_HASH:
        raise RuntimeError("API_ID and API_HASH are required for Pyrogram importer")


def build_client() -> Client:
    require_userbot_config()
    return Client(
        name=config.USERBOT_SESSION_NAME,
        api_id=config.API_ID,
        api_hash=config.API_HASH,
        session_string=config.USERBOT_SESSION_STRING,
        no_updates=True,
    )


def normalize_chat_id(value: str) -> int | str:
    stripped = value.strip()
    if stripped.lstrip("-").isdigit():
        return int(stripped)
    return stripped


def read_downloaded_media(downloaded: BinaryIO | str | None) -> tuple[bytes, str | None]:
    if downloaded is None:
        raise RuntimeError("Pyrogram returned no downloaded media")
    if isinstance(downloaded, str):
        with open(downloaded, "rb") as file:
            return file.read(), downloaded

    downloaded.seek(0)
    return downloaded.read(), getattr(downloaded, "name", None)


def message_photo_ids(message: Message) -> tuple[str, str | None]:
    if not message.photo:
        raise RuntimeError("Message does not contain a photo")
    return message.photo.file_id, message.photo.file_unique_id


def message_chat_id(message: Message) -> int | None:
    return getattr(getattr(message, "chat", None), "id", None)


def message_published_at(message: Message) -> datetime | None:
    if message.date is None:
        return None
    if message.date.tzinfo is None:
        return message.date.replace(tzinfo=UTC)
    return message.date.astimezone(UTC)


def should_backfill_photo(photo: Photo) -> bool:
    return (
        photo.sha256 is None
        or photo.perceptual_hash is None
        or photo.content_type is None
        or photo.file_size is None
    )


async def download_photo_message(app: Client, message: Message) -> tuple[bytes, str | None]:
    downloaded = await app.download_media(message, in_memory=True)
    return read_downloaded_media(downloaded)


async def backfill_photo_hashes(app: Client, message: Message, photo: Photo, file_id: str) -> bool:
    if not should_backfill_photo(photo):
        async with async_session() as session:
            photo = await session.merge(photo)
            await update_photo_metadata(session, photo, telegram_file_id=file_id)
        return False

    data, file_path = await download_photo_message(app, message)
    metadata = photo_metadata_from_bytes(data=data, file_path=file_path)

    async with async_session() as session:
        photo = await session.merge(photo)
        await update_photo_metadata(
            session,
            photo,
            telegram_file_id=file_id,
            content_type=metadata.content_type,
            file_size=metadata.file_size,
            sha256=metadata.sha256,
            perceptual_hash=metadata.perceptual_hash,
        )

    return True


async def import_photo_message(app: Client, message: Message, dry_run: bool) -> ImportResult:
    if not message.photo:
        return ImportResult(imported=False)

    file_id, file_unique_id = message_photo_ids(message)

    if dry_run:
        logger.info(
            "Would import chat_id=%s message_id=%s file_unique_id=%s published_at=%s media_group_id=%s caption=%s",
            message_chat_id(message),
            message.id,
            file_unique_id,
            message_published_at(message),
            message.media_group_id,
            bool(message.caption),
        )
        return ImportResult(imported=True)

    async with async_session() as session:
        existing_history = await get_channel_history_item(
            session,
            chat_id=message_chat_id(message),
            message_id=message.id,
        )
        photo = await get_photo_by_telegram_unique_id(session, file_unique_id)

    uploaded = False
    reused = photo is not None
    backfilled = False

    if photo is None:
        data, file_path = await download_photo_message(app, message)
        stored_photo = await upload_photo_bytes(
            data=data,
            file_id=file_id,
            file_unique_id=file_unique_id,
            source="channel-history",
            file_path=file_path,
        )

        async with async_session() as session:
            photo = await create_photo(
                session,
                telegram_file_id=stored_photo.telegram_file_id,
                telegram_file_unique_id=stored_photo.telegram_file_unique_id,
                storage_bucket=stored_photo.storage_bucket,
                storage_key=stored_photo.storage_key,
                content_type=stored_photo.content_type,
                file_size=stored_photo.file_size,
                sha256=stored_photo.sha256,
                perceptual_hash=stored_photo.perceptual_hash,
            )
        uploaded = True
    else:
        backfilled = await backfill_photo_hashes(app, message, photo, file_id)

    async with async_session() as session:
        await create_channel_history_item(
            session,
            chat_id=message_chat_id(message),
            message_id=message.id,
            file_id=file_id,
            photo_id=photo.id,
            published_at=message_published_at(message),
            caption=message.caption,
            media_group_id=message.media_group_id,
        )

    logger.info(
        "Imported channel photo message_id=%s photo_id=%s uploaded=%s reused=%s backfilled=%s already_indexed=%s",
        message.id,
        photo.id,
        uploaded,
        reused,
        backfilled,
        existing_history is not None,
    )
    return ImportResult(
        imported=True,
        uploaded=uploaded,
        reused=reused,
        backfilled=backfilled,
        already_indexed=existing_history is not None,
    )


async def run() -> None:
    args = parse_args()
    stats = ImportStats()
    channel = normalize_chat_id(args.channel)

    async with build_client() as app:
        async for message in app.get_chat_history(channel, limit=args.limit or 0):
            stats.scanned += 1
            if message.photo:
                stats.photos += 1
            try:
                result = await import_photo_message(app, message, dry_run=args.dry_run)
                stats.record(result)
            except Exception:
                stats.failed += 1
                logger.exception("Failed to import message_id=%s", getattr(message, "id", None))

    logger.info(
        "Channel import finished: scanned=%s photos=%s imported=%s uploaded=%s reused=%s "
        "backfilled=%s already_indexed=%s failed=%s",
        stats.scanned,
        stats.photos,
        stats.imported,
        stats.uploaded,
        stats.reused,
        stats.backfilled,
        stats.already_indexed,
        stats.failed,
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())


if __name__ == "__main__":
    main()
