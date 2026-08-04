import asyncio
from sqlalchemy import select
from db.database import async_session
from db.models import PhotoTournament


async def main():
    async with async_session() as session:
        tournaments = (await session.execute(select(PhotoTournament.id, PhotoTournament.status))).all()
        for t in tournaments:
            print(f"Tournament {t.id} - Status: {t.status}")


asyncio.run(main())
