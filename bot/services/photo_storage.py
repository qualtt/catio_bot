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
from PIL import Image, UnidentifiedImageError

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
    perceptual_hash: str | None


@dataclass(frozen=True)
class PhotoMetadata:
    content_type: str
    file_size: int
    sha256: str
    perceptual_hash: str | None


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


def compute_perceptual_hash(data: bytes, hash_size: int = 8) -> str | None:
    try:
        with Image.open(io.BytesIO(data)) as image:
            grayscale = image.convert("L").resize(
                (hash_size + 1, hash_size),
                Image.Resampling.LANCZOS,
            )
    except (OSError, UnidentifiedImageError):
        return None

    pixels = list(grayscale.getdata())
    value = 0
    for row in range(hash_size):
        row_offset = row * (hash_size + 1)
        for col in range(hash_size):
            value <<= 1
            if pixels[row_offset + col] > pixels[row_offset + col + 1]:
                value |= 1
    return f"{value:0{hash_size * hash_size // 4}x}"


def hamming_distance(left: str | None, right: str | None) -> int | None:
    if not left or not right:
        return None
    try:
        return (int(left, 16) ^ int(right, 16)).bit_count()
    except ValueError:
        return None


def photo_metadata_from_bytes(
    *,
    data: bytes,
    file_path: str | None = None,
    content_type: str | None = None,
) -> PhotoMetadata:
    return PhotoMetadata(
        content_type=content_type or mimetypes.guess_type(file_path or "")[0] or "image/jpeg",
        file_size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        perceptual_hash=compute_perceptual_hash(data),
    )


async def upload_photo_bytes(
    *,
    data: bytes,
    file_id: str,
    file_unique_id: str | None,
    source: str,
    file_path: str | None = None,
    content_type: str | None = None,
) -> StoredPhoto:
    bucket = _require_bucket()
    metadata = photo_metadata_from_bytes(data=data, file_path=file_path, content_type=content_type)
    storage_key = _storage_key(source, metadata.sha256, file_path)

    def upload() -> None:
        _s3_client().put_object(
            Bucket=bucket,
            Key=storage_key,
            Body=data,
            ContentType=metadata.content_type,
        )

    await asyncio.to_thread(upload)

    return StoredPhoto(
        telegram_file_id=file_id,
        telegram_file_unique_id=file_unique_id,
        storage_bucket=bucket,
        storage_key=storage_key,
        content_type=metadata.content_type,
        file_size=metadata.file_size,
        sha256=metadata.sha256,
        perceptual_hash=metadata.perceptual_hash,
    )


async def upload_telegram_photo(
    bot: Bot,
    *,
    file_id: str,
    file_unique_id: str | None,
    source: str,
) -> StoredPhoto:
    telegram_file = await bot.get_file(file_id)

    buffer = io.BytesIO()
    await bot.download_file(telegram_file.file_path, destination=buffer)
    return await upload_photo_bytes(
        data=buffer.getvalue(),
        file_id=file_id,
        file_unique_id=file_unique_id,
        source=source,
        file_path=telegram_file.file_path,
    )


async def download_photo(*, storage_bucket: str, storage_key: str) -> bytes:
    def download() -> bytes:
        response = _s3_client().get_object(Bucket=storage_bucket, Key=storage_key)
        return response["Body"].read()

    return await asyncio.to_thread(download)
