from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import config
from bot.services.photo_storage import hamming_distance
from db.models.channel_history import ChannelHistory
from db.models.photo import Photo
from db.models.post import Post, PostStatus
from db.models.user import User


@dataclass(frozen=True)
class DuplicatePhotoMatch:
    photo_id: int
    distance: int
    reason: str


POPULARITY_STATUSES = [PostStatus.APPROVED, PostStatus.PUBLISHED]


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


async def get_photo_by_id(session: AsyncSession, photo_id: int) -> Photo | None:
    return await session.get(Photo, photo_id)


async def photo_has_public_usage(session: AsyncSession, photo_id: int) -> bool:
    history_count = await session.scalar(
        select(func.count(ChannelHistory.id)).where(ChannelHistory.photo_id == photo_id)
    )
    if history_count:
        return True

    published_post_count = await session.scalar(
        select(func.count(Post.id)).where(
            Post.photo_id == photo_id,
            Post.status == PostStatus.PUBLISHED,
        )
    )
    return bool(published_post_count)


async def user_can_view_photo(
    session: AsyncSession,
    *,
    photo_id: int,
    telegram_id: int,
    is_admin: bool = False,
) -> bool:
    if is_admin:
        return True

    if await photo_has_public_usage(session, photo_id):
        return True

    own_post_count = await session.scalar(
        select(func.count(Post.id))
        .join(User, User.id == Post.user_id)
        .where(
            Post.photo_id == photo_id,
            User.telegram_id == telegram_id,
        )
    )
    return bool(own_post_count)


async def get_random_public_photo(session: AsyncSession) -> Photo | None:
    stmt = (
        select(Photo)
        .where(
            (Photo.channel_history_items.any())
            | (Photo.posts.any(Post.status == PostStatus.PUBLISHED))
        )
        .order_by(func.random())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


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

    has_usage = or_(
        select(Post.id).where(Post.photo_id == Photo.id).correlate(Photo).exists(),
        select(ChannelHistory.id).where(ChannelHistory.photo_id == Photo.id).correlate(Photo).exists()
    )

    if photo.sha256:
        stmt = select(Photo).where(Photo.id != photo.id, Photo.sha256 == photo.sha256, has_usage)
        exact = (await session.execute(stmt)).scalar_one_or_none()
        if exact:
            return DuplicatePhotoMatch(photo_id=exact.id, distance=0, reason="exact")

    if not photo.perceptual_hash:
        return None

    threshold = config.DUPLICATE_PHASH_MAX_DISTANCE if max_distance is None else max_distance
    stmt = select(Photo.id, Photo.perceptual_hash).where(
        Photo.id != photo.id,
        Photo.perceptual_hash.is_not(None),
        has_usage
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



from datetime import datetime

from sqlalchemy import delete, update


async def get_abandoned_photos(session: AsyncSession, older_than: datetime) -> list[Photo]:
    has_usage = or_(
        select(Post.id).where(
            Post.photo_id == Photo.id,
            Post.status.in_([PostStatus.PENDING, PostStatus.APPROVED, PostStatus.PUBLISHED])
        ).correlate(Photo).exists(),
        select(ChannelHistory.id).where(ChannelHistory.photo_id == Photo.id).correlate(Photo).exists()
    )

    stmt = select(Photo).where(Photo.created_at < older_than, ~has_usage)
    return list((await session.execute(stmt)).scalars().all())

async def delete_photos(session: AsyncSession, photo_ids: list[int]) -> None:
    if not photo_ids:
        return
    
    await session.execute(
        update(Post)
        .where(Post.photo_id.in_(photo_ids), Post.status == PostStatus.REJECTED)
        .values(photo_id=None)
    )
    
    await session.execute(
        update(Post)
        .where(Post.duplicate_of_photo_id.in_(photo_ids))
        .values(duplicate_of_photo_id=None)
    )
    
    await session.execute(
        delete(Photo).where(Photo.id.in_(photo_ids))
    )
    await session.commit()
