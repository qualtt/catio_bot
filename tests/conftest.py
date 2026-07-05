import os
from pathlib import Path

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


ROOT = Path(__file__).resolve().parents[1]

ENV_DEFAULTS = {
    "BOT_TOKEN": "123:abc",
    "ADMIN_ID": "1",
    "CHANNEL_ID": "-100123",
    "POSTGRES_USER": "user",
    "POSTGRES_PASSWORD": "password",
    "POSTGRES_DB": "catio_test",
    "POSTGRES_HOST": "localhost",
    "POSTGRES_PORT": "5432",
    "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
    "BOT_CONTENT_PATH": str(ROOT / "bot_content.json"),
    "S3_BUCKET": "test-bucket",
}

for key, value in ENV_DEFAULTS.items():
    os.environ.setdefault(key, value)

import db.models  # noqa: E402,F401
from db.models import Base  # noqa: E402


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
        await session.rollback()

    await engine.dispose()
