from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    BOT_TOKEN: str
    ADMIN_ID: int
    CHANNEL_ID: str

    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_DB: str
    POSTGRES_HOST: str
    POSTGRES_PORT: int
    DATABASE_URL: str

    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379

    TIMEZONE: str = "Europe/Moscow"
    AUTO_POST_TIME_HOUR: int = 11
    AUTO_POST_TIME_MINUTE: int = 0
    AUTO_POST_DAYS_AHEAD: int = 30
    DAILY_SLOT_TIMES: str = "11:00"
    PUBLISHER_POLL_INTERVAL_SECONDS: int = 60
    BOT_CONTENT_PATH: str = "bot_content.json"
    DUPLICATE_PHASH_MAX_DISTANCE: int = 8

    SCORE_APPROVED_POST_BASE: int = 100
    SCORE_AUTO_BONUS_MIN_PERCENT: int = 10
    SCORE_AUTO_BONUS_MAX_PERCENT: int = 20
    SCORE_PENDING_POST_WEIGHT_PERCENT: int = 50
    SCORE_DUPLICATE_EXACT_FACTOR_PERCENT: int = 0
    SCORE_DUPLICATE_SIMILAR_FACTOR_PERCENT: int = 50
    SCORE_OLD_PHOTO_CORRECT: int = 3
    SCORE_OLD_PHOTO_DAILY_CAP: int = 30

    IDENTIFICATION_VOTES_REQUIRED: int = 1
    IDENTIFICATION_CONSENSUS_PERCENT: int = 67
    IDENTIFICATION_MAX_VOTES_PER_PHOTO: int = 7
    IDENTIFICATION_ASSIGNMENT_TTL_MINUTES: int = 30
    IDENTIFICATION_BATCH_SIZE: int = 10
    IDENTIFICATION_MAX_ACTIVE_ASSIGNMENTS_PER_PHOTO: int = 3

    S3_ENDPOINT_URL: str | None = None
    S3_REGION: str = "us-east-1"
    S3_BUCKET: str | None = None
    S3_ACCESS_KEY_ID: str | None = None
    S3_SECRET_ACCESS_KEY: str | None = None
    S3_PREFIX: str = "catio-bot/photos"
    S3_FORCE_PATH_STYLE: bool = False

    API_ID: int | None = None
    API_HASH: str | None = None
    USERBOT_SESSION_STRING: str | None = None
    USERBOT_SESSION_NAME: str = "catio_importer"

    @field_validator("API_ID", mode="before")
    @classmethod
    def empty_api_id_to_none(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator(
        "API_HASH",
        "USERBOT_SESSION_STRING",
        "S3_ENDPOINT_URL",
        "S3_BUCKET",
        "S3_ACCESS_KEY_ID",
        "S3_SECRET_ACCESS_KEY",
        mode="before",
    )
    @classmethod
    def empty_string_to_none(cls, value: Any) -> Any:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

config = Settings()
