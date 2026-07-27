from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.post import Post, PostStatus
from db.models.user import User


async def get_or_create_user(session: AsyncSession, telegram_id: int, username: str | None = None, full_name: str | None = None) -> User:
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


async def add_user_score(session: AsyncSession, user_id: int, score_to_add: int):
    stmt = select(User).where(User.id == user_id)
    result = await session.execute(stmt)
    user = result.scalar_one()
    user.score += score_to_add
    await session.commit()


async def get_top_users(session: AsyncSession, limit: int = 10) -> list[User]:
    stmt = select(User).order_by(User.score.desc(), User.id.asc()).limit(limit)
    return list((await session.execute(stmt)).scalars())


async def get_top_users_by_posts(session: AsyncSession, limit: int = 10) -> list[tuple[User, int]]:
    stmt = (
        select(User, func.count(Post.id))
        .join(Post, Post.user_id == User.id)
        .where(Post.status.in_([PostStatus.APPROVED, PostStatus.PUBLISHED]))
        .group_by(User.id)
        .order_by(func.count(Post.id).desc(), User.id.asc())
        .limit(limit)
    )
    return list(await session.execute(stmt))


