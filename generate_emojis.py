import json
import os
import time
from io import BytesIO

import requests
from PIL import Image, ImageDraw, ImageFont

# ==========================================
# НАСТРОЙКИ СРИПТА
# ==========================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
USER_ID = 422987826  # Твой user_id в Telegram (число)
BOT_USERNAME = "kotoblockbot" # Имя бота БЕЗ @ (например, MyCalendarBot)

# Имя пака должно обязательно заканчиваться на _by_<bot_username>
# Например: calendar_numbers_by_MyCalendarBot
PACK_NAME = f"calendar_red_by_{BOT_USERNAME}"
PACK_TITLE = "Calendar Red"

COLORS = {
    "red": "#e53935",
}

# Эмодзи, которые будут ассоциироваться с цветами
# Позволит при вводе 🟢 получать все зеленые числа в подсказках
COLOR_EMOJIS = {
    "red": "🔴"
}
# ==========================================


def generate_emoji_image(number, color_hex):
    """Генерирует изображение эмодзи (100x100 WEBP) с числом в круге"""
    size = 100
    img = Image.new("RGBA", (size, size), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    
    # Рисуем круг
    margin = 2
    draw.ellipse((margin, margin, size - margin, size - margin), fill=color_hex)
    
    # Подбираем шрифт
    font_size = 55 if len(str(number)) > 1 else 60
    try:
        # Стандартный шрифт для macOS
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", font_size)
    except OSError:
        try:
            # Запасной вариант
            font = ImageFont.truetype("arial.ttf", font_size)
        except OSError:
            font = ImageFont.load_default()
            
    text = str(number)
    
    # Центрируем текст
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    
    x = (size - w) / 2
    # Небольшая корректировка по Y из-за базовой линии шрифта
    y = (size - h) / 2 - bbox[1]
    
    draw.text((x, y), text, fill="white", font=font)
    
    # Сохраняем в память в формате WEBP (требование Telegram)
    buf = BytesIO()
    img.save(buf, format="WEBP")
    buf.seek(0)
    return buf.getvalue()


def create_set_with_first_sticker(sticker_data, emoji):
    """Создает новый набор стикеров с первым эмодзи"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/createNewStickerSet"
    stickers = [{
        "sticker": "attach://file_0",
        "emoji_list": [emoji]
    }]
    
    data = {
        "user_id": USER_ID,
        "name": PACK_NAME,
        "title": PACK_TITLE,
        "sticker_type": "custom_emoji",
        "sticker_format": "static",
        "stickers": json.dumps(stickers)
    }
    files = {
        "file_0": ("sticker.webp", sticker_data, "image/webp")
    }
    
    response = requests.post(url, data=data, files=files).json()
    return response


def add_sticker(sticker_data, emoji):
    """Добавляет эмодзи в существующий набор"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/addStickerToSet"
    sticker = {
        "sticker": "attach://file_0",
        "emoji_list": [emoji]
    }
    
    data = {
        "user_id": USER_ID,
        "name": PACK_NAME,
        "sticker": json.dumps(sticker)
    }
    files = {
        "file_0": ("sticker.webp", sticker_data, "image/webp")
    }
    
    response = requests.post(url, data=data, files=files).json()
    return response


def main():
    if not BOT_TOKEN or BOT_TOKEN == "ТВОЙ_ТОКЕН_БОТА":
        print("❌ ОШИБКА: Пожалуйста, укажите BOT_TOKEN в переменных окружения или в начале скрипта!")
        return

    print("🎨 Генерируем изображения...")
    stickers = []
    
    for color_name, color_hex in COLORS.items():
        base_emoji = COLOR_EMOJIS[color_name]
        for num in range(1, 32):
            img_bytes = generate_emoji_image(num, color_hex)
            stickers.append((img_bytes, base_emoji))
            
    print(f"✅ Сгенерировано {len(stickers)} эмодзи (1 цвет по 31 числу).")
    print("🚀 Начинаем загрузку в Telegram...")
    
    first_img, first_emoji = stickers[0]
    
    print("Создаем новый пак...")
    res = create_set_with_first_sticker(first_img, first_emoji)
    
    if not res.get("ok"):
        if "name is already occupied" in str(res):
            print("⚠️ Пак с таким именем уже существует! Продолжаем добавлять в него...")
        else:
            print("❌ Ошибка при создании пака:", res)
            return
    else:
        print("✅ Пак успешно создан!")
        
    # Добавляем остальные стикеры
    for i, (img_bytes, emoji) in enumerate(stickers[1:], start=2):
        time.sleep(0.2) # Небольшая задержка, чтобы не словить Too Many Requests от Telegram
        print(f"Добавляем эмодзи {i}/{len(stickers)}...")
        
        res = add_sticker(img_bytes, emoji)
        if not res.get("ok"):
            if "STICKER_ALREADY_IN_SET" in str(res):
                print(f"⚠️ Эмодзи {i} уже есть в паке.")
            else:
                print(f"❌ Ошибка при добавлении эмодзи {i}:", res)
            
    print(f"\n🎉 Готово! Пак доступен по ссылке: https://t.me/addstickers/{PACK_NAME}")
    print("❗️ Обрати внимание: новые эмодзи могут появиться в клиенте не сразу (иногда нужно подождать пару минут или перезапустить приложение).")

if __name__ == "__main__":
    main()
