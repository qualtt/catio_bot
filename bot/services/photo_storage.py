from __future__ import annotations

import asyncio
import hashlib
import io
import mimetypes
from dataclasses import dataclass
from pathlib import PurePosixPath

import boto3
from aiogram import Bot
from botocore.config import Config

from bot.config import config


@dataclass(frozen=True)
class StoredPhoto:
    telegram_file_id: str
    telegram_file_unique_id: str | None
    storage_bucket: str
    storage_key: str
    content_type: str
    file_size: int
    sha256: str


def _require_bucket() -> str:
    if not config.S3_BUCKET:
        raise RuntimeError("S3_BUCKET is not configured")
    return config.S3_BUCKET


def _s3_client():
    client_config = Config(
        s3={"addressing_style": "path" if config.S3_FORCE_PATH_STYLE else "auto"},
    )
    kwargs = {
        "region_name": config.S3_REGION,
        "config": client_config,
    }
    if config.S3_ENDPOINT_URL:
        kwargs["endpoint_url"] = config.S3_ENDPOINT_URL
    if config.S3_ACCESS_KEY_ID and config.S3_SECRET_ACCESS_KEY:
        kwargs["aws_access_key_id"] = config.S3_ACCESS_KEY_ID
        kwargs["aws_secret_access_key"] = config.S3_SECRET_ACCESS_KEY
    return boto3.client("s3", **kwargs)


def _storage_key(source: str, sha256: str, file_path: str | None) -> str:
    prefix = config.S3_PREFIX.strip("/")
    source_segment = source.strip("/").replace("..", "") or "photos"
    suffix = PurePosixPath(file_path or "").suffix.lower() or ".jpg"
    if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
        suffix = ".jpg"
    key = f"{source_segment}/{sha256[:2]}/{sha256}{suffix}"
    return f"{prefix}/{key}" if prefix else key


async def upload_telegram_photo(
    bot: Bot,
    *,
    file_id: str,
    file_unique_id: str | None,
    source: str,
) -> StoredPhoto:
    bucket = _require_bucket()
    telegram_file = await bot.get_file(file_id)

    buffer = io.BytesIO()
    await bot.download_file(telegram_file.file_path, destination=buffer)
    data = buffer.getvalue()
    sha256 = hashlib.sha256(data).hexdigest()
    storage_key = _storage_key(source, sha256, telegram_file.file_path)
    content_type = mimetypes.guess_type(telegram_file.file_path or "")[0] or "image/jpeg"

    def upload() -> None:
        _s3_client().put_object(
            Bucket=bucket,
            Key=storage_key,
            Body=data,
            ContentType=content_type,
        )

    await asyncio.to_thread(upload)

    return StoredPhoto(
        telegram_file_id=file_id,
        telegram_file_unique_id=file_unique_id,
        storage_bucket=bucket,
        storage_key=storage_key,
        content_type=content_type,
        file_size=len(data),
        sha256=sha256,
    )


async def download_photo(*, storage_bucket: str, storage_key: str) -> bytes:
    def download() -> bytes:
        response = _s3_client().get_object(Bucket=storage_bucket, Key=storage_key)
        return response["Body"].read()

    return await asyncio.to_thread(download)
