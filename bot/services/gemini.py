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
        animal_types = await get_animal_type_options(session, is_primary=True)
    names = [at.name for at in animal_types]
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
            f"{{\"animal\": \"{options_str}\", \"is_valid\": true/false, \"reason\": \"почему\"}}. "
            "is_valid = false если это мем, рисунок, человек или фото без животного."
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
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, proxy=config.GEMINI_PROXY_URL, timeout=aiohttp.ClientTimeout(total=60)) as response:
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
        if result.get("is_valid") and result.get("animal") not in valid_options:
            logger.warning("Gemini returned invalid animal type: %s", result.get("animal"))
            result["is_valid"] = False
            result["reason"] = f"Нейросеть не смогла определить тип из списка ({result.get('animal')})."
            
        return result
    except Exception:
        logger.exception("Failed to analyze photo with Gemini")
        return None
