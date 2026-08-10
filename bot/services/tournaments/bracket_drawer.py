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


async def generate_tournament_bracket_image(
    session: AsyncSession,
    tournament_id: int,
    user_id: int | None = None,
) -> bytes | None:
    from db.models.photo_tournament import PhotoTournamentVote

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

    user_votes: dict[int, int] = {}
    user_views = {}
    if user_id is not None:
        vote_records = (
            await session.scalars(
                select(PhotoTournamentVote).where(
                    PhotoTournamentVote.tournament_id == tournament_id,
                    PhotoTournamentVote.user_id == user_id,
                )
            )
        ).all()
        user_votes = {v.match_id: v.chosen_entry_id for v in vote_records}

        from bot.services.tournaments.voting import resolve_user_match_view

        for m in matches:
            v = await resolve_user_match_view(session, user_id=user_id, match=m)
            if v:
                user_views[m.id] = v

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
    ROW_HEIGHT = 220

    leaf_count = sum(1 for m in matches if not m.feeder_left_match_id and not m.feeder_right_match_id)
    if leaf_count == 0:
        leaf_count = 1

    width = (max_round + 1) * COL_WIDTH + 50
    height = leaf_count * ROW_HEIGHT
    height = max(height, ROW_HEIGHT * 2)

    img = Image.new("RGB", (width, height), "#141414")
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
        bold_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
    except Exception:
        try:
            font = ImageFont.truetype("Arial.ttf", 16)
            bold_font = ImageFont.truetype("Arial.ttf", 20)
        except Exception:
            font = ImageFont.load_default()
            bold_font = ImageFont.load_default()

    current_y = 0

    def assign_coords(node: BracketNode) -> int:
        nonlocal current_y
        node.x = (node.round_idx - 1) * COL_WIDTH + 50

        if not node.feeder_left and not node.feeder_right:
            node.y = current_y + ROW_HEIGHT // 2
            current_y += ROW_HEIGHT
        else:
            left_y = assign_coords(node.feeder_left) if node.feeder_left else None
            right_y = assign_coords(node.feeder_right) if node.feeder_right else None

            if left_y is not None and right_y is not None:
                node.y = (left_y + right_y) // 2
            elif left_y is not None:
                node.y = left_y
            elif right_y is not None:
                node.y = right_y

        return node.y

    assign_coords(root_node)
    height = max(current_y, ROW_HEIGHT * 2)

    photos_to_fetch = {}
    for m in matches:
        uv = user_views.get(m.id)
        l_ent = uv.left_entry if uv else m.left_entry
        r_ent = uv.right_entry if uv else m.right_entry
        if l_ent and l_ent.photo:
            photos_to_fetch[l_ent.photo.id] = l_ent.photo
        if r_ent and r_ent.photo:
            photos_to_fetch[r_ent.photo.id] = r_ent.photo
        if m.winner_entry and m.winner_entry.photo:
            photos_to_fetch[m.winner_entry.photo.id] = m.winner_entry.photo

    import asyncio

    fetched_images = {}
    winner_images = {}

    async def fetch_task(p_id, p):
        im = await _fetch_photo_image(p.storage_bucket, p.storage_key)
        if im:
            fetched_images[p_id] = _create_thumbnail(im, size=(80, 80))
            winner_images[p_id] = _create_thumbnail(im, size=(120, 120))

    await asyncio.gather(*(fetch_task(p_id, p) for p_id, p in photos_to_fetch.items()))

    BOX_WIDTH = 250
    BOX_HEIGHT = 190

    def get_plural_votes(n: int) -> str:
        if n % 10 == 1 and n % 100 != 11:
            return f"{n} голос"
        elif 2 <= n % 10 <= 4 and (n % 100 < 10 or n % 100 >= 20):
            return f"{n} голоса"
        return f"{n} голосов"

    def draw_node(node: BracketNode):
        if node.feeder_left and node.feeder_right:
            draw_node(node.feeder_left)
            draw_node(node.feeder_right)

            line_color = "#3a3a3c"
            line_w = 3

            draw.line(
                [(node.feeder_left.x + BOX_WIDTH, node.feeder_left.y), (node.x - 20, node.feeder_left.y)],
                fill=line_color,
                width=line_w,
            )
            draw.line([(node.x - 20, node.feeder_left.y), (node.x - 20, node.y)], fill=line_color, width=line_w)

            draw.line(
                [(node.feeder_right.x + BOX_WIDTH, node.feeder_right.y), (node.x - 20, node.feeder_right.y)],
                fill=line_color,
                width=line_w,
            )
            draw.line([(node.x - 20, node.feeder_right.y), (node.x - 20, node.y)], fill=line_color, width=line_w)

            draw.line([(node.x - 20, node.y), (node.x, node.y)], fill=line_color, width=line_w)

        box_rect = [node.x, node.y - BOX_HEIGHT // 2, node.x + BOX_WIDTH, node.y + BOX_HEIGHT // 2]

        uv = user_views.get(node.match.id)
        left_entry = uv.left_entry if uv else node.match.left_entry
        right_entry = uv.right_entry if uv else node.match.right_entry

        chosen_id = user_votes.get(node.match.id)
        has_winner = node.match.winner_entry_id is not None

        draw.rounded_rectangle(box_rect, radius=12, fill="#2c2c2e", outline="#48484a", width=1)

        if left_entry:
            l_photo = fetched_images.get(left_entry.photo.id)
            if l_photo:
                img.paste(l_photo, (node.x + 10, node.y - 85), l_photo)

            if user_id is not None and chosen_id == left_entry.id:
                l_votes_text = "ВЫБОР ✓"
                c = "#34c759"
            elif user_id is not None:
                l_votes_text = get_plural_votes(node.match.left_votes)
                c = "#8e8e93"
            else:
                l_votes_text = get_plural_votes(node.match.left_votes)
                c = "#34c759" if has_winner and node.match.winner_entry_id == left_entry.id else "#8e8e93"

            draw.text((node.x + 100, node.y - 55), l_votes_text, fill=c, font=font)

        draw.line([(node.x + 100, node.y), (node.x + 240, node.y)], fill="#48484a", width=1)

        if right_entry:
            r_photo = fetched_images.get(right_entry.photo.id)
            if r_photo:
                img.paste(r_photo, (node.x + 10, node.y + 5), r_photo)

            if user_id is not None and chosen_id == right_entry.id:
                r_votes_text = "ВЫБОР ✓"
                c = "#34c759"
            elif user_id is not None:
                r_votes_text = get_plural_votes(node.match.right_votes)
                c = "#8e8e93"
            else:
                r_votes_text = get_plural_votes(node.match.right_votes)
                c = "#34c759" if has_winner and node.match.winner_entry_id == right_entry.id else "#8e8e93"

            draw.text((node.x + 100, node.y + 35), r_votes_text, fill=c, font=font)
        elif not right_entry and left_entry:
            draw.text((node.x + 100, node.y + 35), "АВТОПРОХОД", fill="#8e8e93", font=font)

    draw_node(root_node)

    winner_x = root_node.x + COL_WIDTH
    winner_y = root_node.y
    WINNER_BOX_W = 180
    WINNER_BOX_H = 200

    draw.line([(root_node.x + BOX_WIDTH, root_node.y), (winner_x, winner_y)], fill="#ffcc00", width=4)

    w_box = [winner_x, winner_y - WINNER_BOX_H // 2, winner_x + WINNER_BOX_W, winner_y + WINNER_BOX_H // 2]
    draw.rounded_rectangle(w_box, radius=16, fill="#34c759", outline="#248a3d", width=2)

    root_uv = user_views.get(root_match.id)
    root_left = root_uv.left_entry if root_uv else root_match.left_entry
    root_right = root_uv.right_entry if root_uv else root_match.right_entry
    root_chosen = user_votes.get(root_match.id)

    winner_entry = None
    if user_id is not None and root_chosen:
        if root_left and root_chosen == root_left.id:
            winner_entry = root_left
        elif root_right and root_chosen == root_right.id:
            winner_entry = root_right
    elif root_match.winner_entry:
        winner_entry = root_match.winner_entry

    if winner_entry and winner_entry.photo:
        w_photo = winner_images.get(winner_entry.photo.id)
        if w_photo:
            img.paste(w_photo, (winner_x + 30, winner_y - WINNER_BOX_H // 2 + 15), w_photo)

    winner_label = "ВАШ ВЫБОР!" if (user_id is not None and root_chosen) else "ПОБЕДИТЕЛЬ!"
    bbox = draw.textbbox((0, 0), winner_label, font=bold_font)
    text_w = bbox[2] - bbox[0]
    text_x = winner_x + (WINNER_BOX_W - text_w) // 2
    draw.text((text_x, winner_y + 55), winner_label, fill="#ffffff", font=bold_font)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
