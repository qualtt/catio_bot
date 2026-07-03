from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from bot.config import config

engine = create_async_engine(config.DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, expire_on_commit=False)

async def get_session():
    async with async_session() as session:
        yield session
