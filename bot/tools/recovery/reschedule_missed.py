"""Move approved posts whose time has already passed to the front of the queue, shifting the rest.

The publisher only picks posts scheduled within the last hour, so anything that fell into
downtime would never be published. This puts those posts into the earliest slots from --start
on, and re-packs every auto-scheduled queued post after them using the bot's own slot rules
(get_next_auto_slot: DAILY_SLOT_TIMES, at most one cat per day). Posts whose time a user picked
by hand and that are still in the future keep their time.

Run it against the production database right before the bot starts; it is safe to re-run.

Usage:
    python -m bot.tools.recovery.reschedule_missed --database-url URL [--start "2026-11-07 00:00"] [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import os
from datetime import datetime


async def run(start_text: str | None, dry_run: bool) -> None:
    from sqlalchemy import select

    from db.crud.schedule import get_next_auto_slot
    from db.crud.time_utils import app_timezone, now_in_app_tz
    from db.database import async_session, engine
    from db.models.post import Post, PostStatus

    tz = app_timezone()
    start = datetime.strptime(start_text, "%Y-%m-%d %H:%M").replace(tzinfo=tz) if start_text else now_in_app_tz()

    async with async_session() as session:
        approved = (
            await session.scalars(
                select(Post)
                .where(Post.status == PostStatus.APPROVED, Post.schedule_time.is_not(None))
                .order_by(Post.schedule_time, Post.id)
            )
        ).all()
        missed = [p for p in approved if p.schedule_time < start]
        if not missed:
            print(f"Нет одобренных постов со временем раньше {start:%Y-%m-%d %H:%M}. Ничего не меняю.")
            await engine.dispose()
            return

        movable = missed + [p for p in approved if p.schedule_time >= start and p.is_auto_scheduled]
        old = {p.id: p.schedule_time for p in movable}
        for post in movable:
            post.schedule_time = None
        await session.flush()

        for post in movable:
            post.schedule_time = await get_next_auto_slot(session, animal_type=post.animal_type, start_at=start)
            await session.flush()

        print(f"Старт: {start:%Y-%m-%d %H:%M}. Пропущено: {len(missed)}, сдвинуто всего: {len(movable)}")
        for post in movable:
            before = old[post.id].astimezone(tz)
            after = post.schedule_time.astimezone(tz)
            mark = "пропущен" if post in missed else "сдвиг   "
            print(f"  #{post.id:<4} {mark} {post.animal_type or '?':<8} {before:%d.%m %H:%M} -> {after:%d.%m %H:%M}")

        if dry_run:
            await session.rollback()
            print("dry-run: изменения не сохранены")
        else:
            await session.commit()
            print("сохранено")
    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--start", help='first moment a post may go out, "YYYY-MM-DD HH:MM" (default: now)')
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    os.environ["DATABASE_URL"] = args.database_url
    asyncio.run(run(args.start, args.dry_run))


if __name__ == "__main__":
    main()
