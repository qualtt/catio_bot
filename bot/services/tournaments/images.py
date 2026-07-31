from __future__ import annotations

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


async def tournament_match_photo_input(view: TournamentMatchView) -> BufferedInputFile:
    left_photo = view.left_entry.photo
    right_photo = view.right_entry.photo
    left_data = await download_photo(
        storage_bucket=left_photo.storage_bucket,
        storage_key=left_photo.storage_key,
    )
    right_data = await download_photo(
        storage_bucket=right_photo.storage_bucket,
        storage_key=right_photo.storage_key,
    )
    return BufferedInputFile(
        _compose_match_image(left_data, right_data),
        filename=f"tournament-{view.match.id}.jpg",
    )


async def tournament_entry_photo_input(
    entry: PhotoTournamentEntry,
) -> BufferedInputFile:
    photo = entry.photo
    photo_data = await download_photo(
        storage_bucket=photo.storage_bucket,
        storage_key=photo.storage_key,
    )
    return BufferedInputFile(photo_data, filename=f"tournament-entry-{entry.id}.jpg")
