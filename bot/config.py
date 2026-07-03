from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    BOT_TOKEN: str
    ADMIN_ID: int
    CHANNEL_ID: str

    API_ID: int
    API_HASH: str
    USERBOT_SESSION_STRING: str | None = None

    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_DB: str
    POSTGRES_HOST: str
    POSTGRES_PORT: int
    DATABASE_URL: str

    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379

    AUTO_POST_TIME_HOUR: int = 11
    AUTO_POST_TIME_MINUTE: int = 0
    AUTO_POST_DAYS_AHEAD: int = 30

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

config = Settings()
