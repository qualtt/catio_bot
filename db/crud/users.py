from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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


