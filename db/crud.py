from datetime import datetime, timedelta, date
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from db.models.user import User
from db.models.post import Post, PostStatus
from bot.config import config

async def get_or_create_user(session: AsyncSession, telegram_id: int, username: str = None, full_name: str = None) -> User:
    stmt = select(User).where(User.telegram_id == telegram_id)
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()
    if not user:
        user = User(telegram_id=telegram_id, username=username, full_name=full_name)
        session.add(user)
        await session.commit()
        await session.refresh(user)
    return user

async def get_next_auto_slot(session: AsyncSession) -> datetime:
    """
    Finds the next available auto-slot.
    Starts from tomorrow up to AUTO_POST_DAYS_AHEAD.
    If all are filled, it will add a second post to the earliest day.
    """
    # This is a simplified logic. In reality, we query the DB to count posts per day
    # for the next N days.
    start_date = date.today() + timedelta(days=1)
    end_date = start_date + timedelta(days=config.AUTO_POST_DAYS_AHEAD)
    
    # Get counts of approved/published posts per day
    stmt = select(func.date(Post.schedule_time), func.count(Post.id)).where(
        Post.status.in_([PostStatus.APPROVED, PostStatus.PUBLISHED]),
        Post.schedule_time >= start_date,
        Post.schedule_time < end_date
    ).group_by(func.date(Post.schedule_time))
    
    result = await session.execute(stmt)
    counts = dict(result.all()) # {date_obj: count}
    
    # Find the day with the minimum count, preferring earlier dates
    min_count = None
    best_date = None
    
    for i in range(config.AUTO_POST_DAYS_AHEAD):
        curr_date = start_date + timedelta(days=i)
        curr_count = counts.get(curr_date, 0)
        
        if min_count is None or curr_count < min_count:
            min_count = curr_count
            best_date = curr_date
            if min_count == 0:
                break # Found a completely empty day!
                
    # Combine with default time
    return datetime.combine(best_date, datetime.min.time()).replace(
        hour=config.AUTO_POST_TIME_HOUR, 
        minute=config.AUTO_POST_TIME_MINUTE
    )

async def create_post(session: AsyncSession, user_id: int, file_id: str, animal_type: str, is_auto_scheduled: bool = False, manual_time: datetime = None) -> Post:
    post = Post(
        user_id=user_id,
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
