from fastapi import APIRouter, Query
from pydantic import BaseModel

from db.crud import get_top_users, get_top_users_by_posts, get_top_users_by_tournaments
from db.database import async_session

router = APIRouter(prefix="/leaderboard", tags=["Leaderboard"])


class LeaderboardEntry(BaseModel):
    position: int
    user_id: int
    telegram_id: int
    name: str
    username: str | None
    value: int


@router.get("", response_model=list[LeaderboardEntry])
async def get_leaderboard(
    type: str = Query("score", enum=["score", "posts", "tournaments"]),
    limit: int = Query(20, ge=1, le=100),
):
    async with async_session() as session:
        if type == "score":
            users = await get_top_users(session, limit=limit)
            result = []
            for idx, user in enumerate(users, start=1):
                name = user.full_name or user.username or f"User {user.telegram_id}"
                result.append(
                    LeaderboardEntry(
                        position=idx,
                        user_id=user.id,
                        telegram_id=user.telegram_id,
                        name=name,
                        username=user.username,
                        value=user.score,
                    )
                )
            return result

        elif type == "posts":
            data = await get_top_users_by_posts(session, limit=limit)
            result = []
            for idx, (user, count) in enumerate(data, start=1):
                name = user.full_name or user.username or f"User {user.telegram_id}"
                result.append(
                    LeaderboardEntry(
                        position=idx,
                        user_id=user.id,
                        telegram_id=user.telegram_id,
                        name=name,
                        username=user.username,
                        value=count,
                    )
                )
            return result

        else:  # tournaments
            data = await get_top_users_by_tournaments(session, limit=limit)
            result = []
            for idx, (user, count) in enumerate(data, start=1):
                name = user.full_name or user.username or f"User {user.telegram_id}"
                result.append(
                    LeaderboardEntry(
                        position=idx,
                        user_id=user.id,
                        telegram_id=user.telegram_id,
                        name=name,
                        username=user.username,
                        value=count,
                    )
                )
            return result
