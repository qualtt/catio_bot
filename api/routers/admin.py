from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from api.auth import get_current_admin
from db.crud import (
    approve_post,
    get_pending_posts,
    get_post_by_id,
    get_scheduled_posts_for_date,
    reject_post,
)
from db.database import async_session
from db.models.user import User

router = APIRouter(prefix="/admin", tags=["Admin"])


class PendingPostResponse(BaseModel):
    id: int
    photo_id: int | None
    image_url: str | None
    user_id: int
    user_name: str | None
    animal_type: str | None
    created_at: str


class ApprovePostRequest(BaseModel):
    schedule_time: str | None = None  # ISO format string or None for auto
    animal_type: str | None = None


class RejectPostRequest(BaseModel):
    reason: str | None = None


@router.get("/pending", response_model=list[PendingPostResponse])
async def get_admin_pending_posts(admin: Annotated[User, Depends(get_current_admin)]):
    async with async_session() as session:
        posts = await get_pending_posts(session)

    res = []
    for p in posts:
        name = p.user.full_name or p.user.username if p.user else "Anonymous"
        res.append(
            PendingPostResponse(
                id=p.id,
                photo_id=p.photo_id,
                image_url=f"/api/v1/photos/{p.photo_id}/image" if p.photo_id else None,
                user_id=p.user_id,
                user_name=name,
                animal_type=p.animal_type,
                created_at=p.created_at.isoformat() if p.created_at else "",
            )
        )
    return res


@router.post("/posts/{post_id}/approve")
async def admin_approve_post(
    post_id: int,
    payload: ApprovePostRequest,
    admin: Annotated[User, Depends(get_current_admin)],
):
    async with async_session() as session:
        post = await get_post_by_id(session, post_id)
        if not post:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")

        updated_post = await approve_post(
            session,
            post_id=post_id,
            schedule_time=payload.schedule_time,
            animal_type=payload.animal_type or post.animal_type,
        )

    return {"success": True, "post_id": updated_post.id, "status": "APPROVED"}


@router.post("/posts/{post_id}/reject")
async def admin_reject_post(
    post_id: int,
    payload: RejectPostRequest,
    admin: Annotated[User, Depends(get_current_admin)],
):
    async with async_session() as session:
        post = await get_post_by_id(session, post_id)
        if not post:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")

        updated_post = await reject_post(session, post_id=post_id, reason=payload.reason)

    return {"success": True, "post_id": updated_post.id, "status": "REJECTED"}


@router.get("/schedule")
async def get_admin_schedule(
    target_date: str = Query(..., description="Date in YYYY-MM-DD format"),
    admin: Annotated[User, Depends(get_current_admin)] = None,
):
    try:
        parsed_date = date.fromisoformat(target_date)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid date format, expected YYYY-MM-DD")

    async with async_session() as session:
        posts = await get_scheduled_posts_for_date(session, parsed_date)

    res = []
    for p in posts:
        res.append(
            {
                "id": p.id,
                "photo_id": p.photo_id,
                "image_url": f"/api/v1/photos/{p.photo_id}/image" if p.photo_id else None,
                "animal_type": p.animal_type,
                "schedule_time": p.schedule_time.isoformat() if p.schedule_time else None,
                "user_name": p.user.full_name or p.user.username if p.user else "Anonymous",
            }
        )
    return res
