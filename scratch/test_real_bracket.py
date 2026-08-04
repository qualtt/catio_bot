import asyncio
import os
import sys

# Добавляем корень проекта в PYTHONPATH
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from db.database import async_session
from db.models.photo_tournament import PhotoTournament, TOURNAMENT_COMPLETED, TOURNAMENT_RUNNING
from bot.services.tournaments.bracket_drawer import generate_tournament_bracket_image


async def main():
    async with async_session() as session:
        # Ищем завершенный турнир
        stmt = (
            select(PhotoTournament)
            .where(PhotoTournament.status.in_([TOURNAMENT_COMPLETED, TOURNAMENT_RUNNING]))
            .order_by(PhotoTournament.id.desc())
        )
        tournament = (await session.scalars(stmt)).first()

        if not tournament:
            print(
                "Не найдено ни одного турнира в БД. Запустите турнир в боте и подождите завершения (или попробуйте на проде)."
            )
            return

        print(f"Генерируем сетку для турнира {tournament.id}...")

        # Генерируем картинку
        image_bytes = await generate_tournament_bracket_image(session, tournament.id)

        if image_bytes:
            filename = f"scratch/bracket_{tournament.id}.png"
            with open(filename, "wb") as f:
                f.write(image_bytes)
            print(f"Успех! Картинка сохранена в файл {filename}")
        else:
            print("Не удалось сгенерировать картинку. Возможно, турнир пустой или нет матчей.")


if __name__ == "__main__":
    asyncio.run(main())
