from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import mimetypes
from dataclasses import dataclass
from pathlib import PurePosixPath

import boto3
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from botocore.config import Config
from PIL import Image, UnidentifiedImageError

from bot.config import config

logger = logging.getLogger(__name__)


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


import botocore.httpsession

original_get_proxy_manager = botocore.httpsession.URLLib3Session._get_proxy_manager


def _patched_get_proxy_manager(self, proxy_url):
    if proxy_url not in self._proxy_managers:
        proxy_headers = self._proxy_config.proxy_headers_for(proxy_url)
        proxy_ssl_context = self._setup_proxy_ssl_context(proxy_url)
        proxy_manager_kwargs = self._get_pool_manager_kwargs(proxy_headers=proxy_headers)
        proxy_manager_kwargs.update(self._proxies_kwargs(proxy_ssl_context=proxy_ssl_context))

        if proxy_url.startswith(("socks5:", "socks5h:")):
            from urllib3.contrib.socks import SOCKSProxyManager

            proxy_manager = SOCKSProxyManager(proxy_url, **proxy_manager_kwargs)
        else:
            proxy_manager = botocore.httpsession.proxy_from_url(proxy_url, **proxy_manager_kwargs)

        proxy_manager.pool_classes_by_scheme = self._pool_classes_by_scheme
        self._proxy_managers[proxy_url] = proxy_manager

    return self._proxy_managers[proxy_url]


botocore.httpsession.URLLib3Session._get_proxy_manager = _patched_get_proxy_manager

original_fix_proxy_url = botocore.httpsession.ProxyConfiguration._fix_proxy_url


def _patched_fix_proxy_url(self, proxy_url):
    if proxy_url.startswith(("socks5:", "socks5h:")):
        return proxy_url
    return original_fix_proxy_url(self, proxy_url)


botocore.httpsession.ProxyConfiguration._fix_proxy_url = _patched_fix_proxy_url


def _s3_client():
    client_config = Config(
        s3={"addressing_style": "path" if config.S3_FORCE_PATH_STYLE else "auto"},
    )
    kwargs = {
        "region_name": config.S3_REGION,
        "config": client_config,
        "verify": config.S3_VERIFY_SSL,
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
    retries = 3
    for attempt in range(retries):
        try:
            telegram_file = await bot.get_file(file_id)
            buffer = io.BytesIO()
            await bot.download_file(telegram_file.file_path, destination=buffer)
            break
        except (TelegramAPIError, asyncio.TimeoutError) as exc:
            if attempt == retries - 1:
                raise
            logger.warning("Retrying upload_telegram_photo after error (attempt %d/%d): %s", attempt + 1, retries, exc)
            await asyncio.sleep(1.0)

    return await upload_photo_bytes(
        data=buffer.getvalue(),
        file_id=file_id,
        file_unique_id=file_unique_id,
        source=source,
        file_path=telegram_file.file_path,
    )


async def download_photo(*, storage_bucket: str, storage_key: str) -> bytes:
    def download() -> bytes:
        import logging
        import time

        import botocore.exceptions

        logger = logging.getLogger(__name__)
        retries = 3
        for attempt in range(retries):
            try:
                response = _s3_client().get_object(Bucket=storage_bucket, Key=storage_key)
                return response["Body"].read()
            except botocore.exceptions.ClientError as e:
                headers = e.response.get("ResponseMetadata", {}).get("HTTPHeaders", {})
                logger.warning(
                    "Boto3 ClientError on download attempt %s/%s: %s (Headers: %s)",
                    attempt + 1,
                    retries,
                    e,
                    headers,
                )
                if attempt == retries - 1:
                    raise
                time.sleep(0.5)

    return await asyncio.to_thread(download)


async def delete_photos_batch(*, storage_bucket: str, storage_keys: list[str]) -> None:
    if not storage_keys:
        return

    def delete() -> None:
        client = _s3_client()
        for i in range(0, len(storage_keys), 1000):
            chunk = storage_keys[i : i + 1000]
            objects = [{"Key": key} for key in chunk]
            client.delete_objects(Bucket=storage_bucket, Delete={"Objects": objects, "Quiet": True})

    await asyncio.to_thread(delete)
