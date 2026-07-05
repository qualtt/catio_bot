from dataclasses import dataclass
from datetime import time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import config
from db.crud import combine_slot, now_in_app_tz, parse_daily_slot_times
from db.models.post import Post, PostStatus
from db.models.score_event import ScoreEvent
from db.models.user import User


POST_APPROVED_EVENT = "post_approved"
OLD_PHOTO_IDENTIFIED_EVENT = "old_photo_identified"


@dataclass(frozen=True)
class ScoreAward:
    points: int
    created: bool
    details: dict[str, Any]


def _clamp_percent(value: int, minimum: int = 0, maximum: int = 100) -> int:
    return max(minimum, min(value, maximum))


def _post_duplicate_factor_percent(post: Post) -> int:
    if post.duplicate_of_photo_id is None:
        return 100
    if post.duplicate_distance == 0:
        return _clamp_percent(config.SCORE_DUPLICATE_EXACT_FACTOR_PERCENT)
    return _clamp_percent(config.SCORE_DUPLICATE_SIMILAR_FACTOR_PERCENT)


async def calculate_auto_bonus_percent(session: AsyncSession) -> int:
    min_bonus = max(config.SCORE_AUTO_BONUS_MIN_PERCENT, 0)
    max_bonus = max(config.SCORE_AUTO_BONUS_MAX_PERCENT, min_bonus)
    tomorrow = now_in_app_tz().date() + timedelta(days=1)
    start_dt = combine_slot(tomorrow, time.min)
    end_dt = combine_slot(tomorrow + timedelta(days=config.AUTO_POST_DAYS_AHEAD), time.min)
    total_slots = max(config.AUTO_POST_DAYS_AHEAD * len(parse_daily_slot_times()), 1)

    approved_count = await session.scalar(
        select(func.count(Post.id)).where(
            Post.status == PostStatus.APPROVED,
            Post.schedule_time >= start_dt,
            Post.schedule_time < end_dt,
        )
    )
    pending_count = await session.scalar(
        select(func.count(Post.id)).where(
            Post.status == PostStatus.PENDING,
            Post.schedule_time >= start_dt,
            Post.schedule_time < end_dt,
        )
    )

    pending_weight = _clamp_percent(config.SCORE_PENDING_POST_WEIGHT_PERCENT) / 100
    effective_posts = (approved_count or 0) + (pending_count or 0) * pending_weight
    coverage = min(effective_posts / total_slots, 1)
    return round(min_bonus + (max_bonus - min_bonus) * (1 - coverage))


async def calculate_post_approval_score(session: AsyncSession, post: Post) -> ScoreAward:
    base_points = max(config.SCORE_APPROVED_POST_BASE, 0)
    auto_bonus_percent = await calculate_auto_bonus_percent(session) if post.is_auto_scheduled else 0
    duplicate_factor_percent = _post_duplicate_factor_percent(post)
    points = round(base_points * (100 + auto_bonus_percent) / 100 * duplicate_factor_percent / 100)
    details = {
        "base_points": base_points,
        "is_auto_scheduled": post.is_auto_scheduled,
        "auto_bonus_percent": auto_bonus_percent,
        "duplicate_factor_percent": duplicate_factor_percent,
        "duplicate_of_photo_id": post.duplicate_of_photo_id,
        "duplicate_distance": post.duplicate_distance,
    }
    return ScoreAward(points=points, created=False, details=details)


async def record_score_event(
    session: AsyncSession,
    *,
    user_id: int,
    event_type: str,
    points: int,
    entity_type: str,
    entity_id: int,
    details: dict[str, Any] | None = None,
) -> ScoreAward:
    existing = await session.scalar(
        select(ScoreEvent).where(
            ScoreEvent.user_id == user_id,
            ScoreEvent.event_type == event_type,
            ScoreEvent.entity_type == entity_type,
            ScoreEvent.entity_id == entity_id,
        )
    )
    if existing:
        return ScoreAward(
            points=existing.points,
            created=False,
            details=existing.details or {},
        )

    user = await session.get(User, user_id)
    if user is None:
        raise ValueError(f"User not found: {user_id}")

    event = ScoreEvent(
        user_id=user_id,
        event_type=event_type,
        points=points,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details,
    )
    session.add(event)
    user.score += points
    await session.flush()
    return ScoreAward(points=points, created=True, details=details or {})


async def award_post_approval_score(session: AsyncSession, post: Post) -> ScoreAward:
    calculation = await calculate_post_approval_score(session, post)
    return await record_score_event(
        session,
        user_id=post.user_id,
        event_type=POST_APPROVED_EVENT,
        points=calculation.points,
        entity_type="post",
        entity_id=post.id,
        details=calculation.details,
    )


async def award_old_photo_identification_score(
    session: AsyncSession,
    *,
    user_id: int,
    channel_history_id: int,
    animal_type: str,
) -> ScoreAward:
    now = now_in_app_tz()
    day_start = combine_slot(now.date(), time.min)
    used_today = await session.scalar(
        select(func.coalesce(func.sum(ScoreEvent.points), 0)).where(
            ScoreEvent.user_id == user_id,
            ScoreEvent.event_type == OLD_PHOTO_IDENTIFIED_EVENT,
            ScoreEvent.created_at >= day_start,
        )
    )
    cap = max(config.SCORE_OLD_PHOTO_DAILY_CAP, 0)
    base_points = max(config.SCORE_OLD_PHOTO_CORRECT, 0)
    available_points = max(cap - int(used_today or 0), 0)
    points = min(base_points, available_points)
    details = {
        "animal_type": animal_type,
        "base_points": base_points,
        "daily_cap": cap,
        "used_today_before_award": int(used_today or 0),
        "capped": points < base_points,
    }
    return await record_score_event(
        session,
        user_id=user_id,
        event_type=OLD_PHOTO_IDENTIFIED_EVENT,
        points=points,
        entity_type="channel_history",
        entity_id=channel_history_id,
        details=details,
    )
