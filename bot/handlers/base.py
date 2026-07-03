from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message
from db.database import async_session
from db.crud import get_or_create_user

base_router = Router()

@base_router.message(CommandStart())
async def start_handler(message: Message):
    async with async_session() as session:
        await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name
        )
    
    await message.answer(
        "Привет! Я бот для предложки фотографий животных в канал.\n\n"
        "Просто отправь мне фото животного, которое хочешь предложить!"
    )

@base_router.message(Command("help"))
async def help_handler(message: Message):
    await message.answer("Отправьте фото, чтобы предложить пост. Мы выберем день и опубликуем его в канале.")
