from dataclasses import dataclass
from datetime import datetime, timedelta, date, time
from zoneinfo import ZoneInfo
from sqlalchemy import and_, select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from db.models.animal_type import AnimalType
from db.models.channel_history import ChannelHistory
from db.models.photo import Photo
from db.models.user import User
from db.models.post import Post, PostStatus
from bot.config import config
from bot.services.photo_storage import hamming_distance


OCCUPYING_STATUSES = [PostStatus.PENDING, PostStatus.APPROVED, PostStatus.PUBLISHED]
POPULARITY_STATUSES = [PostStatus.APPROVED, PostStatus.PUBLISHED]


@dataclass(frozen=True)
class AnimalTypeOption:
    id: int
    name: str
    photo_count: int


@dataclass(frozen=True)
class DuplicatePhotoMatch:
    photo_id: int
    distance: int
    reason: str


def app_timezone() -> ZoneInfo:
    return ZoneInfo(config.TIMEZONE)


def now_in_app_tz() -> datetime:
    return datetime.now(app_timezone())


def parse_daily_slot_times() -> list[time]:
    slots: list[time] = []
    for raw_slot in config.DAILY_SLOT_TIMES.split(","):
        raw_slot = raw_slot.strip()
        if not raw_slot:
            continue
        hour_raw, minute_raw = raw_slot.split(":", 1)
        slots.append(time(hour=int(hour_raw), minute=int(minute_raw)))

    if slots:
        return sorted(slots)

    return [time(hour=config.AUTO_POST_TIME_HOUR, minute=config.AUTO_POST_TIME_MINUTE)]


def combine_slot(target_date: date, slot_time: time) -> datetime:
    return datetime.combine(target_date, slot_time, tzinfo=app_timezone())


def ensure_app_timezone(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=app_timezone())
    return value.astimezone(app_timezone())


def normalize_animal_type(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.split())


async def get_animal_type_options(session: AsyncSession, is_primary: bool) -> list[AnimalTypeOption]:
    photo_count = func.count(Post.id)
    stmt = (
        select(AnimalType.id, AnimalType.name, photo_count.label("photo_count"))
        .outerjoin(
            Post,
            and_(
                func.lower(Post.animal_type) == func.lower(AnimalType.name),
                Post.status.in_(POPULARITY_STATUSES),
            ),
        )
        .where(AnimalType.is_primary == is_primary)
        .group_by(AnimalType.id, AnimalType.name, AnimalType.sort_order)
        .order_by(photo_count.desc(), AnimalType.sort_order.asc(), AnimalType.name.asc())
    )
    result = await session.execute(stmt)
    return [
        AnimalTypeOption(id=animal_type_id, name=name, photo_count=photo_count_value)
        for animal_type_id, name, photo_count_value in result.all()
    ]


async def get_animal_type_name(session: AsyncSession, animal_type_id: int) -> str | None:
    stmt = select(AnimalType.name).where(AnimalType.id == animal_type_id)
    return await session.scalar(stmt)


async def _find_animal_type_by_normalized_name(session: AsyncSession, normalized: str) -> AnimalType | None:
    stmt = select(AnimalType).where(func.lower(AnimalType.name) == normalized.casefold())
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing:
        return existing

    result = await session.execute(select(AnimalType))
    for animal_type in result.scalars():
        if animal_type.name.casefold() == normalized.casefold():
            return animal_type
    return None


async def canonical_animal_type(session: AsyncSession, value: str | None) -> str:
    normalized = normalize_animal_type(value)
    if not normalized:
        return ""

    existing = await _find_animal_type_by_normalized_name(session, normalized)
    return existing.name if existing else normalized


async def ensure_animal_type(session: AsyncSession, value: str | None, is_primary: bool = False) -> AnimalType | None:
    normalized = normalize_animal_type(value)
    if not normalized:
        return None

    existing = await _find_animal_type_by_normalized_name(session, normalized)
    if existing:
        return existing

    max_sort_order = await session.scalar(
        select(func.coalesce(func.max(AnimalType.sort_order), 0)).where(AnimalType.is_primary == is_primary)
    )
    animal_type = AnimalType(
        name=normalized,
        is_primary=is_primary,
        sort_order=max_sort_order + 10,
    )
    session.add(animal_type)
    await session.flush()
    return animal_type


async def get_or_create_user(session: AsyncSession, telegram_id: int, username: str = None, full_name: str = None) -> User:
    stmt = select(User).where(User.telegram_id == telegram_id)
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()
    if not user:
        user = User(telegram_id=telegram_id, username=username, full_name=full_name)
        session.add(user)
        await session.commit()
        await session.refresh(user)
    else:
        user.username = username
        user.full_name = full_name
        await session.commit()
    return user


async def get_photo_by_telegram_unique_id(session: AsyncSession, file_unique_id: str | None) -> Photo | None:
    if not file_unique_id:
        return None

    stmt = select(Photo).where(Photo.telegram_file_unique_id == file_unique_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_photo_by_sha256(session: AsyncSession, sha256: str | None) -> Photo | None:
    if not sha256:
        return None

    stmt = select(Photo).where(Photo.sha256 == sha256)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_channel_history_item_by_message_id(session: AsyncSession, message_id: int) -> ChannelHistory | None:
    stmt = select(ChannelHistory).where(ChannelHistory.message_id == message_id).order_by(ChannelHistory.id.asc()).limit(1)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_channel_history_item(
    session: AsyncSession,
    *,
    message_id: int,
    chat_id: int | None = None,
) -> ChannelHistory | None:
    if chat_id is not None:
        stmt = select(ChannelHistory).where(
            ChannelHistory.chat_id == chat_id,
            ChannelHistory.message_id == message_id,
        )
        item = (await session.execute(stmt)).scalar_one_or_none()
        if item is not None:
            return item

        stmt = select(ChannelHistory).where(
            ChannelHistory.chat_id.is_(None),
            ChannelHistory.message_id == message_id,
        )
        item = (await session.execute(stmt)).scalar_one_or_none()
        if item is not None:
            return item

    return await get_channel_history_item_by_message_id(session, message_id)


async def photo_has_known_usage(session: AsyncSession, photo_id: int) -> bool:
    post_count = await session.scalar(select(func.count(Post.id)).where(Post.photo_id == photo_id))
    if post_count:
        return True

    history_count = await session.scalar(select(func.count(ChannelHistory.id)).where(ChannelHistory.photo_id == photo_id))
    return bool(history_count)


async def find_duplicate_photo(
    session: AsyncSession,
    photo: Photo,
    max_distance: int | None = None,
) -> DuplicatePhotoMatch | None:
    if await photo_has_known_usage(session, photo.id):
        return DuplicatePhotoMatch(photo_id=photo.id, distance=0, reason="exact")

    if photo.sha256:
        stmt = select(Photo).where(Photo.id != photo.id, Photo.sha256 == photo.sha256)
        exact = (await session.execute(stmt)).scalar_one_or_none()
        if exact:
            return DuplicatePhotoMatch(photo_id=exact.id, distance=0, reason="exact")

    if not photo.perceptual_hash:
        return None

    threshold = config.DUPLICATE_PHASH_MAX_DISTANCE if max_distance is None else max_distance
    stmt = select(Photo.id, Photo.perceptual_hash).where(
        Photo.id != photo.id,
        Photo.perceptual_hash.is_not(None),
    )
    result = await session.execute(stmt)
    best_match: DuplicatePhotoMatch | None = None
    for other_photo_id, other_hash in result.all():
        distance = hamming_distance(photo.perceptual_hash, other_hash)
        if distance is None or distance > threshold:
            continue
        if best_match is None or distance < best_match.distance:
            best_match = DuplicatePhotoMatch(photo_id=other_photo_id, distance=distance, reason="similar")

    return best_match


async def create_photo(
    session: AsyncSession,
    *,
    storage_bucket: str,
    storage_key: str,
    telegram_file_id: str | None = None,
    telegram_file_unique_id: str | None = None,
    content_type: str | None = None,
    file_size: int | None = None,
    sha256: str | None = None,
    perceptual_hash: str | None = None,
) -> Photo:
    photo = Photo(
        telegram_file_id=telegram_file_id,
        telegram_file_unique_id=telegram_file_unique_id,
        storage_bucket=storage_bucket,
        storage_key=storage_key,
        content_type=content_type,
        file_size=file_size,
        sha256=sha256,
        perceptual_hash=perceptual_hash,
    )
    session.add(photo)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = await get_photo_by_telegram_unique_id(session, telegram_file_unique_id)
        if existing:
            return await update_photo_metadata(
                session,
                existing,
                telegram_file_id=telegram_file_id,
                content_type=content_type,
                file_size=file_size,
                sha256=sha256,
                perceptual_hash=perceptual_hash,
            )
        existing = await get_photo_by_sha256(session, sha256)
        if existing:
            return await update_photo_metadata(
                session,
                existing,
                telegram_file_id=telegram_file_id,
                telegram_file_unique_id=telegram_file_unique_id,
                content_type=content_type,
                file_size=file_size,
                sha256=sha256,
                perceptual_hash=perceptual_hash,
            )
        raise

    await session.refresh(photo)
    return photo


async def update_photo_metadata(
    session: AsyncSession,
    photo: Photo,
    *,
    telegram_file_id: str | None = None,
    telegram_file_unique_id: str | None = None,
    content_type: str | None = None,
    file_size: int | None = None,
    sha256: str | None = None,
    perceptual_hash: str | None = None,
) -> Photo:
    changed = False

    if telegram_file_id and photo.telegram_file_id != telegram_file_id:
        photo.telegram_file_id = telegram_file_id
        changed = True

    if telegram_file_unique_id and not photo.telegram_file_unique_id:
        existing = await get_photo_by_telegram_unique_id(session, telegram_file_unique_id)
        if existing is None or existing.id == photo.id:
            photo.telegram_file_unique_id = telegram_file_unique_id
            changed = True

    if content_type and not photo.content_type:
        photo.content_type = content_type
        changed = True

    if file_size is not None and photo.file_size is None:
        photo.file_size = file_size
        changed = True

    if sha256 and not photo.sha256:
        existing = await get_photo_by_sha256(session, sha256)
        if existing is None or existing.id == photo.id:
            photo.sha256 = sha256
            changed = True

    if perceptual_hash and not photo.perceptual_hash:
        photo.perceptual_hash = perceptual_hash
        changed = True

    if changed:
        await session.commit()
        await session.refresh(photo)

    return photo


async def create_channel_history_item(
    session: AsyncSession,
    *,
    message_id: int,
    file_id: str,
    chat_id: int | None = None,
    photo_id: int | None = None,
    published_at: datetime | None = None,
    caption: str | None = None,
    media_group_id: str | int | None = None,
    animal_type: str | None = None,
    identified_by: int | None = None,
) -> ChannelHistory:
    normalized_media_group_id = str(media_group_id) if media_group_id is not None else None
    existing = await get_channel_history_item(session, message_id=message_id, chat_id=chat_id)
    if existing:
        if chat_id is not None:
            existing.chat_id = chat_id
        existing.file_id = file_id
        if photo_id is not None:
            existing.photo_id = photo_id
        if published_at is not None:
            existing.published_at = published_at
        existing.caption = caption
        existing.media_group_id = normalized_media_group_id
        if animal_type is not None:
            existing.animal_type = animal_type
        if identified_by is not None:
            existing.identified_by = identified_by
        await session.commit()
        await session.refresh(existing)
        return existing

    item = ChannelHistory(
        chat_id=chat_id,
        message_id=message_id,
        file_id=file_id,
        photo_id=photo_id,
        published_at=published_at,
        caption=caption,
        media_group_id=normalized_media_group_id,
        animal_type=animal_type,
        identified_by=identified_by,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


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

async def get_next_auto_slot(session: AsyncSession) -> datetime:
    """
    Finds the next auto slot on a day without scheduled publications.
    Starts from tomorrow and skips days that already have pending/approved/published posts.
    """
    tomorrow = now_in_app_tz().date() + timedelta(days=1)
    first_slot = parse_daily_slot_times()[0]
    max_days_to_scan = config.AUTO_POST_DAYS_AHEAD + 365
    occupied_dates = await get_occupied_dates(session, start_date=tomorrow, days=max_days_to_scan)

    for day_offset in range(max_days_to_scan):
        curr_date = tomorrow + timedelta(days=day_offset)
        if curr_date not in occupied_dates:
            return combine_slot(curr_date, first_slot)

    return combine_slot(tomorrow + timedelta(days=max_days_to_scan), first_slot)

async def create_post(
    session: AsyncSession,
    user_id: int,
    file_id: str,
    animal_type: str,
    is_auto_scheduled: bool = False,
    manual_time: datetime = None,
    photo_id: int | None = None,
    duplicate_of_photo_id: int | None = None,
    duplicate_distance: int | None = None,
    submission_group_id: str | None = None,
    submission_group_index: int | None = None,
    submission_group_size: int | None = None,
) -> Post:
    post = Post(
        user_id=user_id,
        photo_id=photo_id,
        duplicate_of_photo_id=duplicate_of_photo_id,
        duplicate_distance=duplicate_distance,
        submission_group_id=submission_group_id,
        submission_group_index=submission_group_index,
        submission_group_size=submission_group_size,
        file_id=file_id,
        animal_type=animal_type,
        is_auto_scheduled=is_auto_scheduled,
        schedule_time=manual_time
    )
    session.add(post)
    await session.commit()
    await session.refresh(post)
    return post

async def add_user_score(session: AsyncSession, user_id: int, score_to_add: int):
    stmt = select(User).where(User.id == user_id)
    result = await session.execute(stmt)
    user = result.scalar_one()
    user.score += score_to_add
    await session.commit()


async def get_user_post_stats(session: AsyncSession, user_id: int) -> dict[PostStatus, int]:
    stmt = (
        select(Post.status, func.count(Post.id))
        .where(Post.user_id == user_id)
        .group_by(Post.status)
    )
    result = await session.execute(stmt)
    return dict(result.all())


async def get_recent_user_posts(session: AsyncSession, user_id: int, limit: int = 5) -> list[Post]:
    stmt = (
        select(Post)
        .where(Post.user_id == user_id)
        .order_by(Post.created_at.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars())


async def get_top_users(session: AsyncSession, limit: int = 10) -> list[User]:
    stmt = select(User).order_by(User.score.desc(), User.id.asc()).limit(limit)
    return list((await session.execute(stmt)).scalars())
