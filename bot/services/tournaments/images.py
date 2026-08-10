from __future__ import annotations

import asyncio
import io
import logging

from aiogram.types import BufferedInputFile
from PIL import Image, ImageOps

from bot.services.photo_storage import download_photo
from db.models.photo_tournament import (
    PhotoTournamentEntry,
)

from .models import TournamentMatchView

logger = logging.getLogger(__name__)

_MATCH_FILE_ID_CACHE: dict[int, str] = {}
_MATCH_BYTES_CACHE: dict[int, bytes] = {}
_ENTRY_FILE_ID_CACHE: dict[int, str] = {}
_ENTRY_BYTES_CACHE: dict[int, bytes] = {}


def cache_match_file_id(match_id: int, file_id: str) -> None:
    _MATCH_FILE_ID_CACHE[match_id] = file_id
    _MATCH_BYTES_CACHE.pop(match_id, None)


def cache_entry_file_id(entry_id: int, file_id: str) -> None:
    _ENTRY_FILE_ID_CACHE[entry_id] = file_id
    _ENTRY_BYTES_CACHE.pop(entry_id, None)


def _fit_photo_panel(data: bytes, *, size: tuple[int, int]) -> Image.Image:
    with Image.open(io.BytesIO(data)) as image:
        image = ImageOps.exif_transpose(image).convert("RGB")
        fitted = ImageOps.contain(image, size, Image.Resampling.LANCZOS)

    panel = Image.new("RGB", size, "#111111")
    offset = ((size[0] - fitted.width) // 2, (size[1] - fitted.height) // 2)
    panel.paste(fitted, offset)
    return panel


def _compose_match_image(left_data: bytes, right_data: bytes) -> bytes:
    panel_size = (560, 760)
    gap = 24
    margin = 28
    canvas_size = (panel_size[0] * 2 + gap + margin * 2, panel_size[1] + margin * 2)
    canvas = Image.new("RGB", canvas_size, "#202124")

    positions = [
        (margin, margin),
        (margin + panel_size[0] + gap, margin),
    ]
    for data, position in ((left_data, positions[0]), (right_data, positions[1])):
        canvas.paste(_fit_photo_panel(data, size=panel_size), position)

    output = io.BytesIO()
    canvas.save(output, format="JPEG", quality=88, optimize=True)
    return output.getvalue()


async def tournament_match_photo_input(view: TournamentMatchView) -> str | BufferedInputFile:
    match_id = view.match.id
    if match_id in _MATCH_FILE_ID_CACHE:
        return _MATCH_FILE_ID_CACHE[match_id]

    if match_id in _MATCH_BYTES_CACHE:
        image_bytes = _MATCH_BYTES_CACHE[match_id]
    else:
        left_photo = view.left_entry.photo
        right_photo = view.right_entry.photo
        left_data, right_data = await asyncio.gather(
            download_photo(
                storage_bucket=left_photo.storage_bucket,
                storage_key=left_photo.storage_key,
            ),
            download_photo(
                storage_bucket=right_photo.storage_bucket,
                storage_key=right_photo.storage_key,
            ),
        )
        image_bytes = await asyncio.to_thread(_compose_match_image, left_data, right_data)
        _MATCH_BYTES_CACHE[match_id] = image_bytes

    return BufferedInputFile(
        image_bytes,
        filename=f"tournament-{match_id}.jpg",
    )


async def tournament_entry_photo_input(
    entry: PhotoTournamentEntry,
) -> str | BufferedInputFile:
    if entry.id in _ENTRY_FILE_ID_CACHE:
        return _ENTRY_FILE_ID_CACHE[entry.id]

    photo = entry.photo
    if photo.telegram_file_id:
        return photo.telegram_file_id

    if entry.id in _ENTRY_BYTES_CACHE:
        photo_data = _ENTRY_BYTES_CACHE[entry.id]
    else:
        photo_data = await download_photo(
            storage_bucket=photo.storage_bucket,
            storage_key=photo.storage_key,
        )
        _ENTRY_BYTES_CACHE[entry.id] = photo_data

    return BufferedInputFile(photo_data, filename=f"tournament-entry-{entry.id}.jpg")
