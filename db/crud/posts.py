from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models.post import Post, PostStatus


async def create_post(
    session: AsyncSession,
    user_id: int,
    file_id: str,
    animal_type: str,
    is_auto_scheduled: bool = False,
    manual_time: datetime | None = None,
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


async def get_post_by_id(session: AsyncSession, post_id: int) -> Post | None:
    stmt = (
        select(Post)
        .options(selectinload(Post.user), selectinload(Post.photo))
        .where(Post.id == post_id)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


