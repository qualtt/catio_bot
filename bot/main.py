import asyncio
import logging
from aiogram import Bot, Dispatcher
from bot.config import config
from bot.handlers.base import base_router
# from bot.handlers.suggest import suggest_router
# from bot.handlers.admin import admin_router

logging.basicConfig(level=logging.INFO)

async def main():
    bot = Bot(token=config.BOT_TOKEN)
    dp = Dispatcher()

    dp.include_router(base_router)
    # dp.include_router(suggest_router)
    # dp.include_router(admin_router)

    logging.info("Starting bot...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
