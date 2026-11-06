"""Load reconstruction.json into a fresh database.

Runs the Alembic migrations, then inserts animal types, users, photos, posts and channel history.
Storage keys follow the bot's own layout, so uploading RECOVERY_DIR/photos to S3 under those keys
(upload_s3, a later step) makes every row resolvable. Tournaments, identification votes and score
events are not recoverable and start empty.

Usage:
    python -m bot.tools.recovery.build_db --database-url postgresql+asyncpg://catio_bot:recovery@127.0.0.1:5433/catio_bot_db

Refuses to write into a database that already holds users, photos, posts or channel history.
--replace wipes those tables first, and is only allowed for a database on localhost.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections import Counter
from datetime import datetime
from urllib.parse import urlparse

from bot.tools.recovery.common import RECONSTRUCTION_PATH, REPO_ROOT


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database-url", required=True, help="SQLAlchemy asyncpg URL of the target database")
    parser.add_argument("--replace", action="store_true", help="wipe recovered tables first (localhost only)")
    return parser.parse_args()


def run_migrations() -> None:
    from alembic.config import Config

    from alembic import command

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    command.upgrade(cfg, "head")


async def load(replace: bool, host: str | None) -> None:
    from sqlalchemy import func, select, text

    from bot.config import config
    from bot.services.photo_storage import _storage_key
    from db.crud.animal_types import ensure_animal_type
    from db.crud.time_utils import app_timezone
    from db.database import async_session, engine
    from db.models.channel_history import ChannelHistory
    from db.models.photo import Photo
    from db.models.post import Post, PostStatus
    from db.models.user import User

    data = json.loads(RECONSTRUCTION_PATH.read_text())
    bucket = config.S3_BUCKET or "catio-bot-photos"

    async with async_session() as session:
        counts = {
            model.__tablename__: await session.scalar(select(func.count()).select_from(model))
            for model in (User, Photo, Post, ChannelHistory)
        }
        if any(counts.values()):
            if not replace:
                raise SystemExit(f"Database is not empty: {counts}. Use --replace for a local database.")
            if host not in ("localhost", "127.0.0.1", "::1"):
                raise SystemExit(f"--replace is only allowed for localhost, not {host!r}")
            await session.execute(
                text("TRUNCATE users, photos, posts, channel_history, score_events RESTART IDENTITY CASCADE")
            )
            await session.commit()
            print(f"wiped existing rows: {counts}")

    async with async_session() as session:
        # Animal types: keep the migration's seed if present, add every type seen in the data.
        names = Counter(p["animal_type"] for p in data["posts"] if p["animal_type"])
        names.update(c["animal_type"] for c in data["channel_history"] if c["animal_type"])
        for name in names:
            await ensure_animal_type(session, name)

        user_ids: dict[int, int] = {}
        for u in data["users"]:
            row = User(telegram_id=u["telegram_id"], username=u["username"], full_name=u["full_name"], score=u["score"])
            session.add(row)
            await session.flush()
            user_ids[u["telegram_id"]] = row.id

        first_seen: dict[str, str] = {}
        for p in data["posts"]:
            first_seen.setdefault(p["photo_sha"], p["created_at"])
        for c in data["channel_history"]:
            first_seen.setdefault(c["photo_sha"], c["published_at"])

        photo_ids: dict[str, int] = {}
        seen_unique: set[str] = set()
        for ph in data["photos"]:
            unique_id = ph["telegram_file_unique_id"]
            if unique_id in seen_unique:
                unique_id = None
            if unique_id:
                seen_unique.add(unique_id)
            row = Photo(
                telegram_file_id=ph["telegram_file_id"],
                telegram_file_unique_id=unique_id,
                storage_bucket=bucket,
                storage_key=_storage_key(ph["source"], ph["sha256"], ph["path"]),
                content_type=ph["content_type"],
                file_size=ph["file_size"],
                sha256=ph["sha256"],
                perceptual_hash=ph["perceptual_hash"],
                created_at=datetime.fromisoformat(first_seen.get(ph["sha256"]) or data["generated_at"]),
            )
            session.add(row)
            await session.flush()
            photo_ids[ph["sha256"]] = row.id

        for p in data["posts"]:
            schedule = (
                datetime.strptime(p["schedule"], "%Y-%m-%d %H:%M").replace(tzinfo=app_timezone())
                if p["schedule"]
                else None
            )
            session.add(
                Post(
                    user_id=user_ids[p["user_telegram_id"]],
                    photo_id=photo_ids[p["photo_sha"]],
                    file_id=p["file_id"],
                    animal_type=p["animal_type"],
                    status=PostStatus(p["status"]),
                    schedule_time=schedule,
                    is_auto_scheduled=bool(p["is_auto"]),
                    message_id=p["channel_message_id"],
                    created_at=datetime.fromisoformat(p["created_at"]),
                    submission_group_id=p["group_ref"],
                    submission_group_index=p["group_index"],
                    submission_group_size=p["group_size"],
                )
            )

        for c in data["channel_history"]:
            session.add(
                ChannelHistory(
                    chat_id=c["chat_id"],
                    message_id=c["message_id"],
                    photo_id=photo_ids[c["photo_sha"]],
                    file_id=c["file_id"],
                    published_at=datetime.fromisoformat(c["published_at"]),
                    caption=c["caption"],
                    media_group_id=str(c["media_group_id"]) if c["media_group_id"] else None,
                    animal_type=c["animal_type"],
                )
            )
        await session.commit()

    async with async_session() as session:
        for model in (User, Photo, Post, ChannelHistory):
            print(f"{model.__tablename__}: {await session.scalar(select(func.count()).select_from(model))}")
        by_status = (await session.execute(select(Post.status, func.count()).group_by(Post.status))).all()
        print("posts by status:", {s.value: n for s, n in by_status})
    await engine.dispose()


def main() -> None:
    args = parse_args()
    # bot.config reads DATABASE_URL from the environment before .env, so set it before any import.
    os.environ["DATABASE_URL"] = args.database_url
    run_migrations()
    asyncio.run(load(args.replace, urlparse(args.database_url.replace("+asyncpg", "")).hostname))


if __name__ == "__main__":
    main()
