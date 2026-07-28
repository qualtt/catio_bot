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


async def analyze_photo(bot: Bot, file_id: str) -> dict | None:
    if not config.GEMINI_API_KEY:
        return None
        
    try:
        # Download photo
        telegram_file = await bot.get_file(file_id)
        buffer = io.BytesIO()
        await bot.download_file(telegram_file.file_path, destination=buffer)
        image_data = base64.b64encode(buffer.getvalue()).decode("utf-8")
        
        # Get options
        options_str = await get_animal_prompt_options()
        
        prompt = (
            f"Верни только валидный JSON (без маркдауна и бектиков): "
            f"{{\"animal\": \"{options_str}\", \"is_valid\": true/false, \"reason\": \"почему\", \"comment\": \"твой смешной комментарий\"}}. "
            "is_valid = false если это мем, рисунок, человек или фото без животного. "
            "Если is_valid = true, напиши в поле comment забавный, милый или смешной комментарий (1-2 коротких предложения) о том, что происходит на фото. "
            "Пиши так, будто общаешься с подписчиками. Используй эмодзи. Например, если кот зевает, напиши: 'На кого он так орет 😱'."
        )
        
        payload = {
            "contents": [{
                "role": "user",
                "parts": [
                    {"text": prompt},
                    {
                        "inline_data": {
                            "mime_type": "image/jpeg",
                            "data": image_data
                        }
                    }
                ]
            }],
            "generationConfig": {
                "response_mime_type": "application/json"
            }
        }
        
        # Strip trailing slash if present
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
            post_proxy = None  # aiohttp_socks uses connector, not proxy param
        
        async with aiohttp.ClientSession(**session_kwargs) as session:
            async with session.post(url, json=payload, proxy=post_proxy, timeout=aiohttp.ClientTimeout(total=60)) as response:
                if response.status == 429:
                    logger.warning("Gemini API rate limit exceeded")
                    return None
                
                if response.status != 200:
                    text = await response.text()
                    logger.error("Gemini API error %s: %s", response.status, text)
                    return None
                    
                data = await response.json()
                
        result_text = data["candidates"][0]["content"]["parts"][0]["text"]
        result = json.loads(result_text)
        
        # Validate returned animal
        valid_options = options_str.split("/")
        valid_options_lower = [opt.lower() for opt in valid_options]
        if result.get("is_valid"):
            animal = result.get("animal", "")
            if animal.lower() not in valid_options_lower:
                logger.warning("Gemini returned invalid animal type: %s", animal)
                result["is_valid"] = False
                result["reason"] = f"Нейросеть не смогла определить тип из списка ({animal})."
            else:
                # Сохраняем оригинальный регистр из базы данных
                idx = valid_options_lower.index(animal.lower())
                result["animal"] = valid_options[idx]
            
        return result
    except Exception:
        logger.exception("Failed to analyze photo with Gemini")
        return None
