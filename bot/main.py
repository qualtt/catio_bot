import asyncio
import logging
from contextlib import suppress
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.redis import RedisStorage
from bot.config import config
from bot.handlers.base import base_router
from bot.handlers.suggest import suggest_router
from bot.handlers.admin import admin_router
from bot.handlers.identify import identify_router
from bot.handlers.tournament import tournament_router
from bot.services.publisher import publisher_loop

logging.basicConfig(level=logging.INFO)

async def main():
    bot = Bot(token=config.BOT_TOKEN)
    storage = RedisStorage.from_url(f"redis://{config.REDIS_HOST}:{config.REDIS_PORT}/0")
    dp = Dispatcher(storage=storage)

    dp.include_router(base_router)
    dp.include_router(identify_router)
    dp.include_router(tournament_router)
    dp.include_router(suggest_router)
    dp.include_router(admin_router)

    logging.info("Starting bot...")
    await bot.delete_webhook(drop_pending_updates=True)
    publisher_task = asyncio.create_task(publisher_loop(bot))
    try:
        await dp.start_polling(bot)
    finally:
        publisher_task.cancel()
        with suppress(asyncio.CancelledError):
            await publisher_task
        await storage.close()
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
