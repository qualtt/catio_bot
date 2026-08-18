import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from aiogram.types import CallbackQuery
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from bot.config import config
from bot.content import bot_content
from bot.services.captions import (
    format_schedule,
    submission_caption,
)
from db.crud import (
    app_timezone,
    combine_slot,
    now_in_app_tz,
)
from db.models.post import Post, PostStatus

logger = logging.getLogger(__name__)


def is_admin_user(user_id: int) -> bool:
    return user_id == config.ADMIN_ID


def is_admin(callback: CallbackQuery) -> bool:
    return is_admin_user(callback.from_user.id)


def post_author(post: Post) -> str:
    if post.user and post.user.username:
        return f"@{post.user.username}"
    if post.user:
        return str(post.user.telegram_id)
    return bot_content.message("author_unknown")


def admin_post_caption(post: Post) -> str:
    return submission_caption(
        animal_type=post.animal_type,
        schedule=format_schedule(post.schedule_time),
        author=post_author(post),
        duplicate_of_photo_id=post.duplicate_of_photo_id,
        duplicate_distance=post.duplicate_distance,
    )


def parse_schedule_date(raw_value: str | None) -> date:
    if raw_value == "today" or not raw_value:
        return now_in_app_tz().date()
    return date.fromisoformat(raw_value)


def parse_admin_datetime(raw_value: str, default_date: date | None = None) -> datetime | None:
    value = " ".join(raw_value.split())
    if default_date:
        try:
            time_obj = datetime.strptime(value, "%H:%M").time()  # noqa: DTZ007
            return datetime.combine(default_date, time_obj).replace(tzinfo=app_timezone())
        except ValueError:
            pass

    for date_time_format in ("%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M"):
        try:
            parsed = datetime.strptime(value, date_time_format).replace(tzinfo=ZoneInfo("UTC"))
        except ValueError:
            continue
        return parsed.replace(tzinfo=app_timezone())
    return None


def admin_schedule_text(target_date: date, posts: list[Post]) -> str:
    if not posts:
        return bot_content.message("admin_schedule_empty", date=target_date.isoformat())

    now = now_in_app_tz()
    lines = []
    for post in posts:
        photo_ref = f"/photo_{post.photo_id}" if post.photo_id else bot_content.message("author_unknown")
        time_str = format_schedule(post.schedule_time)[11:16]
        if post.status == PostStatus.APPROVED and post.schedule_time < now - timedelta(hours=1):
            time_str += " ⚠️ Не выложено"
        lines.append(
            bot_content.message(
                "admin_schedule_line",
                post_id=post.id,
                time=time_str,
                animal_type=post.animal_type,
                photo_ref=photo_ref,
                author=post_author(post),
            )
        )
    return bot_content.message(
        "admin_schedule_header",
        date=target_date.isoformat(),
        count=len(posts),
        posts="\n".join(lines),
    )


def admin_post_manage_text(post: Post) -> str:
    return bot_content.message(
        "admin_post_manage",
        post_id=post.id,
        animal_type=post.animal_type,
        status=bot_content.status_label(post.status),
        schedule=format_schedule(post.schedule_time),
        author=post_author(post),
    )


def is_album_post(post: Post) -> bool:
    return bool(post.submission_group_id)


def normalize_rejection_reason(value: str | None) -> str | None:
    reason = " ".join((value or "").split())
    return reason[:500].rstrip() or None


def duplicate_rejection_reason(post: Post) -> str | None:
    if post.duplicate_of_photo_id is None:
        return None

    if post.duplicate_distance == 0:
        return bot_content.message("duplicate_exact_rejection_reason", photo_id=post.duplicate_of_photo_id)

    distance = post.duplicate_distance if post.duplicate_distance is not None else "unknown"
    return bot_content.message(
        "duplicate_similar_rejection_reason",
        photo_id=post.duplicate_of_photo_id,
        distance=distance,
    )


def normalize_duplicate_rejection_reason(value: str | None, post: Post) -> str | None:
    reason = normalize_rejection_reason(value)
    if reason is None:
        return None

    reason_words = set(reason.casefold().replace("ё", "е").split())
    if reason_words & {"копия", "дубль", "дубликат", "повтор"}:
        return duplicate_rejection_reason(post) or reason

    return reason


def approved_user_notification_text(post: Post, *, schedule: str, points: int) -> str:
    if is_album_post(post):
        return bot_content.message(
            "approved_album_user_notification",
            animal_type=post.animal_type,
            schedule=schedule,
            points=points,
        )
    return bot_content.message(
        "approved_user_notification",
        animal_type=post.animal_type,
        schedule=schedule,
        points=points,
    )


def approved_callback_text(post: Post, *, points: int) -> str:
    if is_album_post(post):
        return bot_content.message("approved_album_callback", points=points)
    return bot_content.message("approved_callback", points=points)


def rejected_admin_caption(reason: str | None = None) -> str:
    if reason:
        return bot_content.message("rejected_caption_with_reason", reason=reason)
    return bot_content.message("rejected_caption")


def rejected_user_notification_text(post: Post, *, reason: str | None = None) -> str:
    if is_album_post(post):
        if reason:
            return bot_content.message("rejected_album_user_notification_with_reason", reason=reason)
        return bot_content.message("rejected_album_user_notification")

    if reason:
        return bot_content.message("rejected_user_notification_with_reason", reason=reason)
    return bot_content.message("rejected_user_notification")


async def load_post(session, post_id: int) -> Post | None:
    stmt = select(Post).options(selectinload(Post.user), selectinload(Post.photo)).where(Post.id == post_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def lock_post(session, post_id: int) -> Post | None:
    stmt = (
        select(Post)
        .options(selectinload(Post.user), selectinload(Post.photo))
        .where(Post.id == post_id)
        .with_for_update()
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def load_admin_schedule_posts(session, target_date: date) -> list[Post]:
    day_start = combine_slot(target_date, time.min)
    day_end = combine_slot(target_date, time.max)
    stmt = (
        select(Post)
        .options(selectinload(Post.user))
        .where(
            Post.status == PostStatus.APPROVED,
            Post.schedule_time >= day_start,
            Post.schedule_time <= day_end,
        )
        .order_by(Post.schedule_time.asc(), Post.id.asc())
    )
    return list((await session.execute(stmt)).scalars())


async def load_admin_stats(session) -> str:
    status_rows = await session.execute(select(Post.status, func.count(Post.id)).group_by(Post.status))
    status_counts = dict(status_rows.all())
    now = now_in_app_tz()
    today = now.date()
    today_start = combine_slot(today, time.min)
    today_end = combine_slot(today, time.max)

    today_scheduled = await session.scalar(
        select(func.count(Post.id)).where(
            Post.status == PostStatus.APPROVED,
            Post.schedule_time >= today_start,
            Post.schedule_time <= today_end,
        )
    )
    overdue = await session.scalar(
        select(func.count(Post.id)).where(
            Post.status == PostStatus.APPROVED,
            Post.schedule_time < now,
        )
    )
    next_post = (
        await session.execute(
            select(Post)
            .where(Post.status == PostStatus.APPROVED)
            .order_by(Post.schedule_time.asc(), Post.id.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    animal_rows = await session.execute(
        select(Post.animal_type, func.count(Post.id))
        .where(Post.status.in_([PostStatus.APPROVED, PostStatus.PUBLISHED]))
        .group_by(Post.animal_type)
        .order_by(func.count(Post.id).desc(), Post.animal_type.asc())
        .limit(5)
    )

    from bot.services.tournaments.queries import get_current_tournament
    from db.crud import get_tournament_completed_voter_count, get_tournament_voter_count

    current_tournament = await get_current_tournament(session)
    tournament_voters = 0
    tournament_completed_voters = 0
    if current_tournament:
        tournament_voters = await get_tournament_voter_count(session, current_tournament.id)
        tournament_completed_voters = await get_tournament_completed_voter_count(session, current_tournament.id)
    animal_stats = "\n".join(
        bot_content.message("admin_stats_animal_line", animal_type=animal_type or "?", count=count)
        for animal_type, count in animal_rows.all()
    ) or bot_content.message("admin_stats_no_animals")

    return bot_content.message(
        "admin_stats_text",
        pending=status_counts.get(PostStatus.PENDING, 0),
        approved=status_counts.get(PostStatus.APPROVED, 0),
        rejected=status_counts.get(PostStatus.REJECTED, 0),
        published=status_counts.get(PostStatus.PUBLISHED, 0),
        today_scheduled=today_scheduled or 0,
        overdue=overdue or 0,
        next_schedule=format_schedule(next_post.schedule_time)
        if next_post
        else bot_content.message("schedule_not_selected"),
        animal_stats=animal_stats,
        tournament_voters=tournament_voters,
        tournament_completed_voters=tournament_completed_voters,
    )


async def load_submission_group_posts(session, post: Post) -> list[Post]:
    if not post.submission_group_id:
        return [post]

    stmt = (
        select(Post)
        .options(selectinload(Post.user))
        .where(Post.submission_group_id == post.submission_group_id)
        .order_by(Post.submission_group_index.asc(), Post.id.asc())
    )
    return list((await session.execute(stmt)).scalars())


def callback_is_album_control(callback: CallbackQuery, post: Post) -> bool:
    return bool(post.submission_group_id and callback.message and (callback.message.text or callback.message.photo))


async def _build_admin_reschedule_calendar(year: int, month: int, return_date: date):
    from datetime import timedelta

    from bot.content import bot_content
    from bot.keyboards.calendar import build_month_calendar
    from db.crud import get_day_availability
    from db.crud.time_utils import parse_daily_slot_times
    from db.database import async_session

    today = now_in_app_tz().date()
    min_date = today
    max_date = min_date + timedelta(days=365)

    async with async_session() as session:
        availability = await get_day_availability(session, start_date=min_date, days=365)

    footer_buttons = [
        (
            bot_content.button("cancel"),
            f"admin_cancel_reschedule_{return_date.isoformat()}",
        )
    ]

    return build_month_calendar(
        year=year,
        month=month,
        availability=availability,
        min_date=min_date,
        max_date=max_date,
        max_slots=len(parse_daily_slot_times()),
        footer_buttons=footer_buttons,
        prefix="admin_cal",
    )


async def show_admin_reschedule_calendar(
    message_or_callback,
    post_id: int,
    return_date: date,
    year: int | None = None,
    month: int | None = None,
):
    today = now_in_app_tz().date()
    if year is None or month is None:
        year, month = today.year, today.month

    markup = await _build_admin_reschedule_calendar(year, month, return_date)
    text = f"Выберите дату для публикации #{post_id} (вы можете выбрать любой день):"

    if hasattr(message_or_callback, "message") and message_or_callback.message:
        await message_or_callback.message.edit_text(text, reply_markup=markup)
    else:
        await message_or_callback.answer(text, reply_markup=markup)
