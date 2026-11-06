"""Интерактивный вход в Telegram под аккаунтом админа.

Спрашивает телефон, код из Telegram и (если включён) пароль 2FA,
проверяет, что аккаунт совпадает с ADMIN_ID, и записывает строку сессии
в USERBOT_SESSION_STRING в .env репозитория.

Запуск (в обычном терминале, он интерактивный):
    python -m bot.tools.recovery.login_userbot
Код и пароль никуда, кроме Telegram, не отправляются и не сохраняются.
"""

import asyncio
import re

from pyrogram import Client

from bot.tools.recovery.common import ENV_PATH, env


async def main() -> None:
    env_values = env()
    app = Client(
        "catio_recovery_login",
        api_id=int(env_values["API_ID"]),
        api_hash=env_values["API_HASH"],
        in_memory=True,
        no_updates=True,
    )
    async with app:
        me = await app.get_me()
        session = await app.export_session_string()

    admin_id = env_values.get("ADMIN_ID", "")
    print(f"\nВошли как: {me.first_name} @{me.username} (id {me.id})")
    if str(me.id) != admin_id:
        print(f"ВНИМАНИЕ: этот аккаунт не совпадает с ADMIN_ID ({admin_id}). .env НЕ изменён.")
        return

    text = ENV_PATH.read_text()
    new_line = f"USERBOT_SESSION_STRING={session}"
    if re.search(r"^USERBOT_SESSION_STRING=.*$", text, flags=re.MULTILINE):
        text = re.sub(r"^USERBOT_SESSION_STRING=.*$", lambda _: new_line, text, flags=re.MULTILINE)
    else:
        text = text.rstrip("\n") + "\n" + new_line + "\n"
    ENV_PATH.write_text(text)
    print("Готово: строка сессии записана в .env (USERBOT_SESSION_STRING).")


asyncio.run(main())
