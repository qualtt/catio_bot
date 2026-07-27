from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import config
from db.models.post import Post, PostStatus

from .animal_types import is_cat_animal_type
from .time_utils import (
    combine_slot,
    ensure_app_timezone,
    now_in_app_tz,
    parse_daily_slot_times,
)

OCCUPYING_STATUSES = [PostStatus.PENDING, PostStatus.APPROVED, PostStatus.PUBLISHED]


async def get_slot_counts(session: AsyncSession, start_date: date | None = None, days: int | None = None) -> dict[date, int]:
    start_date = start_date or now_in_app_tz().date()
    days = days or config.AUTO_POST_DAYS_AHEAD
    end_date = start_date + timedelta(days=days)
    start_dt = combine_slot(start_date, time.min)
    end_dt = combine_slot(end_date, time.min)

    stmt = select(func.date(Post.schedule_time), func.count(Post.id)).where(
        Post.status.in_(OCCUPYING_STATUSES),
        Post.schedule_time >= start_dt,
        Post.schedule_time < end_dt
    ).group_by(func.date(Post.schedule_time))

    result = await session.execute(stmt)
    counts: dict[date, int] = {}
    for raw_day, count in result.all():
        if isinstance(raw_day, datetime):
            day = ensure_app_timezone(raw_day).date()
        elif isinstance(raw_day, str):
            day = date.fromisoformat(raw_day)
        else:
            day = raw_day
        counts[day] = count
    return counts


async def get_occupied_dates(session: AsyncSession, start_date: date | None = None, days: int | None = None) -> set[date]:
    start_date = start_date or now_in_app_tz().date()
    days = days or config.AUTO_POST_DAYS_AHEAD
    end_date = start_date + timedelta(days=days)
    start_dt = combine_slot(start_date, time.min)
    end_dt = combine_slot(end_date, time.min)

    stmt = select(Post.schedule_time).where(
        Post.status.in_(OCCUPYING_STATUSES),
        Post.schedule_time >= start_dt,
        Post.schedule_time < end_dt,
    )
    result = await session.execute(stmt)
    return {
        ensure_app_timezone(scheduled_at).date()
        for scheduled_at in result.scalars()
        if scheduled_at is not None
    }


async def get_day_availability(session: AsyncSession, start_date: date | None = None, days: int | None = None) -> dict[date, int]:
    start_date = start_date or now_in_app_tz().date()
    days = days or config.AUTO_POST_DAYS_AHEAD
    max_slots = len(parse_daily_slot_times())
    counts = await get_slot_counts(session, start_date=start_date, days=days)

    availability: dict[date, int] = {}
    for i in range(days):
        curr_date = start_date + timedelta(days=i)
        availability[curr_date] = max(max_slots - counts.get(curr_date, 0), 0)
    return availability


async def get_free_slot_times(session: AsyncSession, target_date: date) -> list[time]:
    day_start = combine_slot(target_date, time.min)
    day_end = combine_slot(target_date + timedelta(days=1), time.min)

    stmt = select(Post.schedule_time).where(
        Post.status.in_(OCCUPYING_STATUSES),
        Post.schedule_time >= day_start,
        Post.schedule_time < day_end,
    )
    result = await session.execute(stmt)
    occupied = {
        ensure_app_timezone(scheduled_at).strftime("%H:%M")
        for scheduled_at in result.scalars()
        if scheduled_at is not None
    }

    return [
        slot_time
        for slot_time in parse_daily_slot_times()
        if slot_time.strftime("%H:%M") not in occupied
    ]


async def get_schedule_occupancy(
    session: AsyncSession,
    start_date: date | None = None,
    days: int | None = None,
) -> tuple[dict[date, set[str]], set[date]]:
    start_date = start_date or now_in_app_tz().date()
    days = days or config.AUTO_POST_DAYS_AHEAD
    end_date = start_date + timedelta(days=days)
    start_dt = combine_slot(start_date, time.min)
    end_dt = combine_slot(end_date, time.min)

    stmt = select(Post.schedule_time, Post.animal_type).where(
        Post.status.in_(OCCUPYING_STATUSES),
        Post.schedule_time >= start_dt,
        Post.schedule_time < end_dt,
    )
    result = await session.execute(stmt)
    occupied_slots: dict[date, set[str]] = {}
    cat_dates: set[date] = set()
    for scheduled_at, animal_type in result.all():
        if scheduled_at is None:
            continue
        scheduled_at = ensure_app_timezone(scheduled_at)
        scheduled_date = scheduled_at.date()
        occupied_slots.setdefault(scheduled_date, set()).add(scheduled_at.strftime("%H:%M"))
        if is_cat_animal_type(animal_type):
            cat_dates.add(scheduled_date)

    return occupied_slots, cat_dates


def _selected_schedule_context(
    selected_slots: set[datetime] | list[datetime] | None,
) -> dict[date, set[str]]:
    selected: dict[date, set[str]] = {}
    for selected_slot in selected_slots or []:
        selected_slot = ensure_app_timezone(selected_slot)
        selected.setdefault(selected_slot.date(), set()).add(selected_slot.strftime("%H:%M"))
    return selected


async def get_next_auto_slot(
    session: AsyncSession,
    *,
    animal_type: str | None = None,
    start_at: datetime | None = None,
    selected_slots: set[datetime] | list[datetime] | None = None,
    selected_cat_dates: set[date] | list[date] | None = None,
) -> datetime:
    """
    Finds the next auto slot.

    Cats are distributed to the nearest day without another cat. Other animals
    can share days with cats or take an empty day. Calls without animal_type keep
    the old empty-day behavior.
    """
    if start_at is None:
        tomorrow = now_in_app_tz().date() + timedelta(days=1)
        start_at = combine_slot(tomorrow, time.min)
    else:
        start_at = ensure_app_timezone(start_at)

    start_date = start_at.date()
    slot_times = parse_daily_slot_times()
    first_slot = slot_times[0]
    max_days_to_scan = config.AUTO_POST_DAYS_AHEAD + 365
    occupied_slots, cat_dates = await get_schedule_occupancy(
        session,
        start_date=start_date,
        days=max_days_to_scan,
    )
    selected_slots_by_date = _selected_schedule_context(selected_slots)
    selected_cat_dates = set(selected_cat_dates or [])
    auto_type_is_cat = is_cat_animal_type(animal_type)

    for day_offset in range(max_days_to_scan):
        curr_date = start_date + timedelta(days=day_offset)
        occupied_for_day = set(occupied_slots.get(curr_date, set()))
        occupied_for_day.update(selected_slots_by_date.get(curr_date, set()))
        has_posts = bool(occupied_for_day)
        has_cat = curr_date in cat_dates or curr_date in selected_cat_dates

        free_slots = [
            slot_time
            for slot_time in slot_times
            if slot_time.strftime("%H:%M") not in occupied_for_day
            and combine_slot(curr_date, slot_time) >= start_at
        ]
        if not free_slots:
            continue

        if animal_type is None:
            if not has_posts:
                return combine_slot(curr_date, free_slots[0])
            continue

        if auto_type_is_cat:
            if not has_cat:
                return combine_slot(curr_date, free_slots[0])
            continue

        if has_cat or not has_posts:
            return combine_slot(curr_date, free_slots[0])

    return combine_slot(start_date + timedelta(days=max_days_to_scan), first_slot)


