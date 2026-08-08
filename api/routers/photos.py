from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from pydantic import BaseModel

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

router = APIRouter(prefix="/photos", tags=["Photos"])


class AnimalTypeResponse(BaseModel):
    id: int
    name: str
    is_primary: bool


class UploadPhotoResponse(BaseModel):
    photo_id: int
    sha256: str
    animal_type: str | None
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

    stored = await upload_photo_bytes(
        data=contents,
        file_id="",  # Web app upload
        file_unique_id=None,
        source="webapp",
        file_path=file.filename,
        content_type=file.content_type,
    )

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
            user_id=current_user.id,
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

    return UploadPhotoResponse(
        photo_id=db_photo.id,
        sha256=db_photo.sha256,
        animal_type=animal_type,
        message="Photo uploaded successfully",
    )
