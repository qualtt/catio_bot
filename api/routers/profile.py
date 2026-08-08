from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.auth import get_current_user
from db.crud import get_recent_user_posts, get_user_post_stats
from db.database import async_session
from db.models.user import User

router = APIRouter(prefix="/profile", tags=["Profile"])


class ProfileResponse(BaseModel):
    id: int
    telegram_id: int
    username: str | None
    full_name: str | None
    score: int
    is_muted: bool
    stats: dict[str, int]


class PostItemResponse(BaseModel):
    id: int
    animal_type: str | None
    status: str
    schedule_time: str | None
    created_at: str


@router.get("/me", response_model=ProfileResponse)
async def get_profile(current_user: Annotated[User, Depends(get_current_user)]):
    async with async_session() as session:
        stats = await get_user_post_stats(session, current_user.id)

    formatted_stats = {}
    for k, v in stats.items():
        key_str = str(k.value if hasattr(k, "value") else k)
        formatted_stats[key_str] = v
        formatted_stats[key_str.upper()] = v

    return ProfileResponse(
        id=current_user.id,
        telegram_id=current_user.telegram_id,
        username=current_user.username,
        full_name=current_user.full_name,
        score=current_user.score,
        is_muted=current_user.is_muted,
        stats=formatted_stats,
    )


@router.get("/posts", response_model=list[PostItemResponse])
async def get_my_posts(current_user: Annotated[User, Depends(get_current_user)], limit: int = 20):
    async with async_session() as session:
        posts = await get_recent_user_posts(session, current_user.id, limit=limit)

    res = []
    for p in posts:
        res.append(
            PostItemResponse(
                id=p.id,
                animal_type=p.animal_type,
                status=p.status.value if hasattr(p.status, "value") else str(p.status),
                schedule_time=p.schedule_time.isoformat() if p.schedule_time else None,
                created_at=p.created_at.isoformat() if p.created_at else "",
            )
        )
    return res
