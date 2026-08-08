import base64
import io
import json
import logging

import aiohttp
from aiogram import Bot

from bot.config import config
from db.database import async_session

logger = logging.getLogger(__name__)


async def get_animal_prompt_options() -> str:
    from db.crud import get_animal_type_options

    async with async_session() as session:
        primary = await get_animal_type_options(session, is_primary=True)
        secondary = await get_animal_type_options(session, is_primary=False)
    names = [at.name for at in primary + secondary]
    return "/".join(names)


async def analyze_photo_bytes(data: bytes) -> dict | None:
    if not config.ENABLE_GEMINI or not config.GEMINI_API_KEY:
        return None

    try:
        image_data = base64.b64encode(data).decode("utf-8")
        options_str = await get_animal_prompt_options()

        prompt = (
            f"Верни только валидный JSON (без маркдауна и бектиков): "
            f'{{"animal": "{options_str}", "is_valid": true/false, "reason": "почему", "comment": "твой смешной комментарий"}}. '
            "is_valid = false если это мем, рисунок, человек или фото без животного. "
            "В поле comment всегда пиши забавный или милый комментарий (1-2 коротких предложения). "
            "Пиши так, будто общаешься с подписчиками. Используй эмодзи. "
            "Если фото валидное, пошути про то, что делает животное (например: 'На кого он так орет 😱'). "
            "Если фото невалидное, пошути над тем, что прислал пользователь (например: 'Это, конечно, красивый стул, но где тут кот? 🤨')."
        )

        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": prompt},
                        {
                            "inline_data": {
                                "mime_type": "image/jpeg",
                                "data": image_data,
                            }
                        },
                    ],
                }
            ],
            "generationConfig": {"response_mime_type": "application/json"},
        }

        base_url = config.GEMINI_BASE_URL.rstrip("/")
        url = f"{base_url}/v1beta/models/{config.GEMINI_MODEL}:generateContent?key={config.GEMINI_API_KEY}"

        session_kwargs = {
            "headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
        }
        post_proxy = config.GEMINI_PROXY_URL

        if config.GEMINI_PROXY_URL and config.GEMINI_PROXY_URL.startswith("socks5"):
            from aiohttp_socks import ProxyConnector

            session_kwargs["connector"] = ProxyConnector.from_url(config.GEMINI_PROXY_URL)
            post_proxy = None

        async with (
            aiohttp.ClientSession(**session_kwargs) as session,
            session.post(
                url,
                json=payload,
                proxy=post_proxy,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as response,
        ):
            if response.status == 429:
                logger.warning("Gemini API rate limit exceeded")
                return None

            if response.status != 200:
                text = await response.text()
                logger.error("Gemini API error %s: %s", response.status, text)
                return None

            data_res = await response.json()

        result_text = data_res["candidates"][0]["content"]["parts"][0]["text"]
        result = json.loads(result_text)

        valid_options = options_str.split("/")
        valid_options_lower = [opt.lower() for opt in valid_options]
        if result.get("is_valid"):
            animal = result.get("animal", "")
            if animal.lower() not in valid_options_lower:
                logger.warning("Gemini returned invalid animal type: %s", animal)
                result["is_valid"] = False
                result["reason"] = f"Нейросеть не смогла определить тип из списка ({animal})."
            else:
                idx = valid_options_lower.index(animal.lower())
                result["animal"] = valid_options[idx]

        return result
    except Exception:
        logger.exception("Failed to analyze photo bytes with Gemini")
        return None


async def analyze_photo(bot: Bot, file_id: str) -> dict | None:
    if not config.GEMINI_API_KEY:
        return None

    try:
        telegram_file = await bot.get_file(file_id)
        buffer = io.BytesIO()
        await bot.download_file(telegram_file.file_path, destination=buffer)
        return await analyze_photo_bytes(buffer.getvalue())
    except Exception:
        logger.exception("Failed to analyze photo with Gemini")
        return None
