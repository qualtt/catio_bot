import hashlib
import hmac
import json
import time
import urllib.parse
from typing import Annotated, Any

import jwt
from fastapi import Depends, HTTPException, Header, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from bot.config import config
from db.crud import get_or_create_user, get_user_by_telegram_id
from db.database import async_session
from db.models.user import User

security = HTTPBearer(auto_error=False)


def validate_telegram_init_data(init_data_raw: str, bot_token: str) -> dict[str, Any]:
    """
    Validates Telegram WebApp initData query string using HMAC-SHA256 algorithm.
    Returns parsed user dict if valid, raises ValueError otherwise.
    """
    try:
        parsed_data = dict(urllib.parse.parse_qsl(init_data_raw, keep_blank_values=True))
    except Exception as err:
        raise ValueError("Invalid query string format") from err

    received_hash = parsed_data.pop("hash", None)
    if not received_hash:
        raise ValueError("Missing hash parameter in initData")

    # Sort remaining parameters lexicographically
    data_check_lines = [f"{k}={v}" for k, v in sorted(parsed_data.items())]
    data_check_string = "\n".join(data_check_lines)

    # Compute secret key HMAC-SHA256("WebAppData", bot_token)
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()

    # Compute data hash HMAC-SHA256(secret_key, data_check_string)
    computed_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        raise ValueError("HMAC signature mismatch")

    # Extract user JSON object
    user_str = parsed_data.get("user")
    if not user_str:
        raise ValueError("Missing user field in initData")

    try:
        user_dict = json.loads(user_str)
    except Exception as err:
        raise ValueError("Invalid user JSON string") from err

    return user_dict


def create_access_token(
    user_id: int, telegram_id: int, is_admin: bool = False, expires_in_seconds: int = 86400 * 7
) -> str:
    now = int(time.time())
    payload = {
        "sub": str(user_id),
        "telegram_id": telegram_id,
        "is_admin": is_admin,
        "iat": now,
        "exp": now + expires_in_seconds,
    }
    return jwt.encode(payload, config.JWT_SECRET, algorithm="HS256")


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, config.JWT_SECRET, algorithms=["HS256"])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has expired")
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


async def get_current_user(
    auth: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> User:
    """
    Dependency that authenticates user either via JWT Bearer token or directly via X-Telegram-Init-Data header.
    """
    # 1. Try JWT token if provided
    if auth and auth.credentials:
        payload = decode_access_token(auth.credentials)
        telegram_id = payload.get("telegram_id")
        if telegram_id:
            async with async_session() as session:
                user = await get_user_by_telegram_id(session, telegram_id)
                if user:
                    return user

    # 2. Direct initData header validation
    if x_telegram_init_data:
        try:
            tg_user = validate_telegram_init_data(x_telegram_init_data, config.BOT_TOKEN)
            async with async_session() as session:
                user = await get_or_create_user(
                    session,
                    telegram_id=tg_user["id"],
                    username=tg_user.get("username"),
                    full_name=f"{tg_user.get('first_name', '')} {tg_user.get('last_name', '')}".strip() or None,
                )
                return user
        except ValueError as err:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(err))

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication credentials were not provided")


async def get_current_admin(user: Annotated[User, Depends(get_current_user)]) -> User:
    if user.telegram_id != config.ADMIN_ID:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required")
    return user
