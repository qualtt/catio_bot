from __future__ import annotations

import io
import logging
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.services.photo_storage import download_photo
from db.models.photo_tournament import PhotoTournamentEntry, PhotoTournamentMatch

logger = logging.getLogger(__name__)


@dataclass
class BracketNode:
    match: PhotoTournamentMatch
    round_idx: int
    row_idx: int
    feeder_left: BracketNode | None = None
    feeder_right: BracketNode | None = None

    # Coordinates assigned during layout
    x: int = 0
    y: int = 0


async def _fetch_photo_image(bucket: str, key: str) -> Image.Image | None:
    try:
        data = await download_photo(storage_bucket=bucket, storage_key=key)
        img = Image.open(io.BytesIO(data))
        img.load()  # verify
        return img
    except Exception as e:
        logger.warning(f"Failed to fetch photo {key} from {bucket}: {e}")
        return None


def _create_thumbnail(img: Image.Image, size: tuple[int, int] = (80, 80)) -> Image.Image:
    # Crop to center square
    w, h = img.size
    min_dim = min(w, h)
    left = (w - min_dim) / 2
    top = (h - min_dim) / 2
    right = (w + min_dim) / 2
    bottom = (h + min_dim) / 2

    img = img.crop((left, top, right, bottom))
    img = img.resize(size, Image.Resampling.LANCZOS)

    # Make it circular
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0) + size, fill=255)

    output = Image.new("RGBA", size, (0, 0, 0, 0))
    output.paste(img, (0, 0))
    output.putalpha(mask)
    return output


async def generate_tournament_bracket_image(session: AsyncSession, tournament_id: int) -> bytes | None:
    # 1. Fetch all matches
    matches = (
        await session.scalars(
            select(PhotoTournamentMatch)
            .options(
                selectinload(PhotoTournamentMatch.left_entry).selectinload(PhotoTournamentEntry.photo),
                selectinload(PhotoTournamentMatch.right_entry).selectinload(PhotoTournamentEntry.photo),
                selectinload(PhotoTournamentMatch.winner_entry).selectinload(PhotoTournamentEntry.photo),
                selectinload(PhotoTournamentMatch.round),
            )
            .where(PhotoTournamentMatch.tournament_id == tournament_id)
            .order_by(PhotoTournamentMatch.id)
        )
    ).all()

    if not matches:
        return None

    # Group matches by round_number
    max_round = max(m.round.round_number for m in matches)

    # Identify the final match (which has no match pointing to it)
    feeder_ids = {m.feeder_left_match_id for m in matches if m.feeder_left_match_id}
    feeder_ids.update({m.feeder_right_match_id for m in matches if m.feeder_right_match_id})

    final_matches = [m for m in matches if m.id not in feeder_ids]
    if not final_matches:
        logger.error(f"Could not find root match for tournament {tournament_id}")
        return None

    root_match = final_matches[0]
    match_by_id = {m.id: m for m in matches}

    def build_tree(match: PhotoTournamentMatch, current_round: int, row: int) -> BracketNode:
        node = BracketNode(match=match, round_idx=current_round, row_idx=row)
        if match.feeder_left_match_id:
            node.feeder_left = build_tree(match_by_id[match.feeder_left_match_id], current_round - 1, row * 2)
        if match.feeder_right_match_id:
            node.feeder_right = build_tree(match_by_id[match.feeder_right_match_id], current_round - 1, row * 2 + 1)
        return node

    root_node = build_tree(root_match, max_round, 0)

    # 2. Dimensions
    COL_WIDTH = 320
    ROW_HEIGHT = 160

    # Find total leaf nodes by traversing
    leaf_count = sum(1 for m in matches if not m.feeder_left_match_id and not m.feeder_right_match_id)
    if leaf_count == 0:
        leaf_count = 1

    width = (max_round + 1) * COL_WIDTH + 50
    height = leaf_count * ROW_HEIGHT

    height = max(height, ROW_HEIGHT * 2)

    img = Image.new("RGB", (width, height), "#141414")
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype("Arial.ttf", 16)
        bold_font = ImageFont.truetype("Arial.ttf", 20)
    except Exception:
        font = ImageFont.load_default()
        bold_font = ImageFont.load_default()

    def assign_coords(node: BracketNode, min_y: int, max_y: int) -> int:
        node.x = (node.round_idx - 1) * COL_WIDTH + 50
        if node.feeder_left and node.feeder_right:
            mid = (min_y + max_y) // 2
            left_y = assign_coords(node.feeder_left, min_y, mid)
            right_y = assign_coords(node.feeder_right, mid, max_y)
            node.y = (left_y + right_y) // 2
        else:
            node.y = (min_y + max_y) // 2
        return node.y

    assign_coords(root_node, 0, height)

    # Fetch all needed photos concurrently
    photos_to_fetch = {}
    for m in matches:
        if m.left_entry and m.left_entry.photo:
            photos_to_fetch[m.left_entry.photo.id] = m.left_entry.photo
        if m.right_entry and m.right_entry.photo:
            photos_to_fetch[m.right_entry.photo.id] = m.right_entry.photo

    import asyncio

    fetched_images = {}

    async def fetch_task(p_id, p):
        im = await _fetch_photo_image(p.storage_bucket, p.storage_key)
        if im:
            fetched_images[p_id] = _create_thumbnail(im)

    await asyncio.gather(*(fetch_task(p_id, p) for p_id, p in photos_to_fetch.items()))

    BOX_WIDTH = 250
    BOX_HEIGHT = 120

    def draw_node(node: BracketNode):
        if node.feeder_left and node.feeder_right:
            draw_node(node.feeder_left)
            draw_node(node.feeder_right)

            line_color = "#3a3a3c"
            line_w = 3

            # Left feeder
            draw.line(
                [(node.feeder_left.x + BOX_WIDTH, node.feeder_left.y), (node.x - 20, node.feeder_left.y)],
                fill=line_color,
                width=line_w,
            )
            draw.line([(node.x - 20, node.feeder_left.y), (node.x - 20, node.y)], fill=line_color, width=line_w)

            # Right feeder
            draw.line(
                [(node.feeder_right.x + BOX_WIDTH, node.feeder_right.y), (node.x - 20, node.feeder_right.y)],
                fill=line_color,
                width=line_w,
            )
            draw.line([(node.x - 20, node.feeder_right.y), (node.x - 20, node.y)], fill=line_color, width=line_w)

            # Join to box
            draw.line([(node.x - 20, node.y), (node.x, node.y)], fill=line_color, width=line_w)

        box_rect = [node.x, node.y - BOX_HEIGHT // 2, node.x + BOX_WIDTH, node.y + BOX_HEIGHT // 2]

        has_winner = node.match.winner_entry_id is not None

        draw.rounded_rectangle(box_rect, radius=12, fill="#2c2c2e", outline="#48484a", width=1)

        if node.match.left_entry:
            l_photo = fetched_images.get(node.match.left_entry.photo.id)
            if l_photo:
                img.paste(l_photo, (node.x + 10, node.y - BOX_HEIGHT // 2 + 10), l_photo)

            l_votes = f"{node.match.left_votes} votes"
            c = "#34c759" if has_winner and node.match.winner_entry_id == node.match.left_entry_id else "#8e8e93"
            draw.text((node.x + 100, node.y - BOX_HEIGHT // 2 + 30), l_votes, fill=c, font=font)

        draw.line([(node.x + 100, node.y), (node.x + 240, node.y)], fill="#48484a", width=1)

        if node.match.right_entry:
            r_photo = fetched_images.get(node.match.right_entry.photo.id)
            if r_photo:
                img.paste(r_photo, (node.x + 10, node.y + 10), r_photo)

            r_votes = f"{node.match.right_votes} votes"
            c = "#34c759" if has_winner and node.match.winner_entry_id == node.match.right_entry_id else "#8e8e93"
            draw.text((node.x + 100, node.y + 30), r_votes, fill=c, font=font)
        elif not node.match.right_entry and node.match.left_entry:
            draw.text((node.x + 100, node.y + 30), "BYE", fill="#8e8e93", font=font)

    draw_node(root_node)

    winner_x = root_node.x + COL_WIDTH
    winner_y = root_node.y
    draw.line([(root_node.x + BOX_WIDTH, root_node.y), (winner_x, winner_y)], fill="#ffcc00", width=4)

    w_box = [winner_x, winner_y - BOX_HEIGHT // 2, winner_x + BOX_WIDTH, winner_y + BOX_HEIGHT // 2]
    draw.rounded_rectangle(w_box, radius=16, fill="#ffcc00", outline="#ff9500", width=2)

    draw.text((winner_x + 80, winner_y - 20), "WINNER!", fill="#000", font=bold_font)

    if root_match.winner_entry:
        w_photo = fetched_images.get(root_match.winner_entry.photo.id)
        if w_photo:
            img.paste(w_photo, (winner_x + 10, winner_y - 40), w_photo)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
