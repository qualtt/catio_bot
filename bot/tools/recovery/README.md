# Восстановление данных из Telegram

Инструменты, которыми данные бота восстанавливали после потери БД и S3 на Beget в ноябре 2026.
Источник — сам Telegram: канал и все личные чаты бота. Всё складывается в `RECOVERY_DIR`
(по умолчанию `~/catio_recovery`). В Telegram ничего не пишется.

Запуск из корня репозитория, по порядку:

```bash
# 1. Вход под аккаунтом админа (интерактивно, в обычном терминале). Пишет USERBOT_SESSION_STRING в .env
.venv/bin/python -m bot.tools.recovery.login_userbot

# 2. Выгрузка: канал (под аккаунтом админа) и все сообщения бота по ID (под токеном бота)
.venv/bin/python -m bot.tools.recovery.export_telegram channel
.venv/bin/python -m bot.tools.recovery.export_telegram bot

# 3. file_id для Bot API и file_unique_id в формате Bot API
.venv/bin/python -m bot.tools.recovery.fetch_file_ids

# 4. Реконструкция: пользователи, фото, история канала, заявки со статусами и доказательствами
.venv/bin/python -m bot.tools.recovery.reconstruct

# 5. Загрузка в пустую БД (миграции применяются сами)
.venv/bin/python -m bot.tools.recovery.build_db --database-url postgresql+asyncpg://USER:PASS@HOST:PORT/DB

# 5a. Заливка фото в S3 из .env (S3_*), с проверкой, что у каждой записи photos есть объект
.venv/bin/python -m bot.tools.recovery.upload_s3 --database-url postgresql+asyncpg://USER:PASS@HOST:PORT/DB

# 6. Перенос пропущенных за время простоя постов в начало очереди со сдвигом остальных.
#    Запускать на боевой БД прямо перед стартом бота (можно повторять)
.venv/bin/python -m bot.tools.recovery.reschedule_missed --database-url postgresql+asyncpg://USER:PASS@HOST:PORT/DB

# 7. Страница для проверки расписания
.venv/bin/python -m bot.tools.recovery.schedule_report --database-url postgresql+asyncpg://USER:PASS@HOST:PORT/DB
open ~/catio_recovery/schedule.html
```

Ручные правки статусов и времени — в `~/catio_recovery/overrides.json` (формат в docstring
`reconstruct.py` и в конце страницы), затем повторить шаги 4–7 (`build_db --replace`
разрешён только для localhost).

Что не восстанавливается: турниры, голоса за определение вида старых фото, история начислений баллов.
Баллы берутся из последнего рейтинга /top или профиля, который бот показывал пользователю,
плюс начисления за одобрения после него; это точно для всех, у кого такой снимок сделан после 25.07.2026.
