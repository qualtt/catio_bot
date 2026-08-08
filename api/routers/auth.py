from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from api.auth import create_access_token, validate_telegram_init_data
from bot.config import config
from db.crud import get_or_create_user
from db.database import async_session

router = APIRouter(prefix="/auth", tags=["Auth"])


class TelegramAuthRequest(BaseModel):
    init_data: str


class UserResponse(BaseModel):
    id: int
    telegram_id: int
    username: str | None
    full_name: str | None
    score: int
    is_admin: bool


class AuthTokenResponse(BaseModel):
    token: str
    user: UserResponse


@router.post("/telegram", response_model=AuthTokenResponse)
async def telegram_auth(payload: TelegramAuthRequest):
    try:
        tg_user = validate_telegram_init_data(payload.init_data, config.BOT_TOKEN)
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err))

    full_name = f"{tg_user.get('first_name', '')} {tg_user.get('last_name', '')}".strip() or None
    is_admin = tg_user["id"] == config.ADMIN_ID

    async with async_session() as session:
        user = await get_or_create_user(
            session,
            telegram_id=tg_user["id"],
            username=tg_user.get("username"),
            full_name=full_name,
        )

    token = create_access_token(user.id, user.telegram_id, is_admin=is_admin)

    return AuthTokenResponse(
        token=token,
        user=UserResponse(
            id=user.id,
            telegram_id=user.telegram_id,
            username=user.username,
            full_name=user.full_name,
            score=user.score,
            is_admin=is_admin,
        ),
    )


@router.post("/dev-login", response_model=AuthTokenResponse)
async def dev_login():
    """
    Development login endpoint for testing outside Telegram WebApp environment.
    Uses ADMIN_ID to allow local admin testing.
    """
    dev_telegram_id = -99999
    async with async_session() as session:
        user = await get_or_create_user(
            session,
            telegram_id=dev_telegram_id,
            username="dev_user",
            full_name="Тестовый Игрок",
        )

    token = create_access_token(user.id, user.telegram_id, is_admin=True)

    return AuthTokenResponse(
        token=token,
        user=UserResponse(
            id=user.id,
            telegram_id=user.telegram_id,
            username=user.username,
            full_name=user.full_name,
            score=user.score,
            is_admin=True,
        ),
    )
