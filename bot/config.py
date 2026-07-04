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

    S3_ENDPOINT_URL: str | None = None
    S3_REGION: str = "us-east-1"
    S3_BUCKET: str | None = None
    S3_ACCESS_KEY_ID: str | None = None
    S3_SECRET_ACCESS_KEY: str | None = None
    S3_PREFIX: str = "catio-bot/photos"
    S3_FORCE_PATH_STYLE: bool = False

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

config = Settings()
