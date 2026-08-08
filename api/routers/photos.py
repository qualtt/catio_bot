from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from pydantic import BaseModel

import logging

from api.auth import get_current_user
from bot.services.photo_storage import download_photo, upload_photo_bytes
from db.crud import (
    get_animal_type_options,
    get_photo_by_id,
)
from db.database import async_session
from db.models.photo import Photo
from db.models.post import Post, PostStatus
from db.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/photos", tags=["Photos"])


from bot.services.gemini import analyze_photo_bytes
from bot.services.photo_storage import optimize_image_bytes


class AnimalTypeResponse(BaseModel):
    id: int
    name: str
    is_primary: bool


class UploadPhotoResponse(BaseModel):
    photo_id: int
    sha256: str
    animal_type: str | None
    ai_comment: str | None = None
    message: str


@router.get("/animal-types", response_model=list[AnimalTypeResponse])
async def get_animal_types():
    async with async_session() as session:
        primary = await get_animal_type_options(session, is_primary=True)
        extra = await get_animal_type_options(session, is_primary=False)

    res = []
    for item in primary:
        res.append(AnimalTypeResponse(id=item.id, name=item.name, is_primary=True))
    for item in extra:
        res.append(AnimalTypeResponse(id=item.id, name=item.name, is_primary=False))
    return res


@router.get("/{photo_id}/image")
async def get_photo_image(photo_id: int):
    async with async_session() as session:
        photo = await get_photo_by_id(session, photo_id)

    if not photo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Photo not found")

    try:
        data = await download_photo(storage_bucket=photo.storage_bucket, storage_key=photo.storage_key)
        return Response(
            content=data,
            media_type=photo.content_type or "image/jpeg",
            headers={"Cache-Control": "public, max-age=86400"},
        )
    except Exception as err:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to fetch photo image: {err}"
        )


@router.post("/upload", response_model=UploadPhotoResponse)
async def upload_photo_file(
    current_user: Annotated[User, Depends(get_current_user)],
    file: UploadFile = File(...),
    animal_type: str | None = Form(None),
):
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only image files are allowed")

    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File is empty")

    contents, content_type = optimize_image_bytes(contents)

    stored = await upload_photo_bytes(
        data=contents,
        file_id="",  # Web app upload
        file_unique_id=None,
        source="webapp",
        file_path=file.filename,
        content_type=content_type,
    )

    ai_analysis = await analyze_photo_bytes(contents)
    ai_comment = None
    if ai_analysis and ai_analysis.get("is_valid"):
        if not animal_type and ai_analysis.get("animal"):
            animal_type = ai_analysis["animal"]
        ai_comment = ai_analysis.get("comment")

    async with async_session() as session:
        db_photo = Photo(
            telegram_file_id="",
            telegram_file_unique_id=None,
            storage_bucket=stored.storage_bucket,
            storage_key=stored.storage_key,
            content_type=stored.content_type,
            file_size=stored.file_size,
            sha256=stored.sha256,
            perceptual_hash=stored.perceptual_hash,
        )
        session.add(db_photo)
        await session.commit()
        await session.refresh(db_photo)

        db_post = Post(
            user_id=current_user.id,
            photo_id=db_photo.id,
            file_id="",
            animal_type=animal_type or "Кот",
            status=PostStatus.PENDING,
        )
        session.add(db_post)
        await session.commit()
        await session.refresh(db_post)

    author_name = current_user.full_name or (
        f"@{current_user.username}" if current_user.username else f"ID: {current_user.telegram_id}"
    )

    async def _notify_admin():
        from aiogram import Bot
        from aiogram.client.default import DefaultBotProperties
        from aiogram.client.session.aiohttp import AiohttpSession
        from aiogram.types import BufferedInputFile

        from bot.config import config
        from bot.handlers.suggest.actions import send_single_submission_to_admin

        if not config.ADMIN_ID:
            return

        proxy = config.TELEGRAM_PROXY_URL
        session = AiohttpSession(proxy=proxy) if proxy else None
        bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"), session=session)

        try:
            input_file = BufferedInputFile(contents, filename="webapp_photo.jpg")
            await send_single_submission_to_admin(
                bot,
                post=db_post,
                file_id=input_file,
                animal_type=animal_type or "Кот",
                schedule_time="На модерации (Mini App)",
                author=f"{author_name} (через Mini App 📱)",
                ai_comment=ai_comment,
            )
        except Exception as err:
            logger.exception("Failed to send admin notification for webapp post %s: %s", db_post.id, err)
        finally:
            await bot.session.close()

    import asyncio

    asyncio.create_task(_notify_admin())

    return UploadPhotoResponse(
        photo_id=db_photo.id,
        sha256=db_photo.sha256,
        animal_type=animal_type,
        ai_comment=ai_comment,
        message="Photo uploaded successfully",
    )
