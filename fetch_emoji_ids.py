import os
import requests

BOT_TOKEN = os.getenv("BOT_TOKEN")
BOT_USERNAME = "kotoblockbot"

PACKS = [
    f"calendar_numbers_by_{BOT_USERNAME}",
    f"calendar_red_by_{BOT_USERNAME}"
]

def fetch_ids():
    mapping = {}
    for pack_name in PACKS:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getStickerSet?name={pack_name}"
        res = requests.get(url).json()
        if not res.get("ok"):
            print(f"Ошибка при получении пака {pack_name}:", res)
            continue
            
        stickers = res["result"]["stickers"]
        # Since we added stickers sequentially (1 to 31), they should be in order
        for i, sticker in enumerate(stickers):
            emoji = sticker["emoji"]
            custom_emoji_id = sticker["custom_emoji_id"]
            
            color_name = "unknown"
            if emoji == "⚫️": color_name = "black"
            elif emoji == "🟡": color_name = "yellow"
            elif emoji == "🟢": color_name = "green"
            elif emoji == "🔴": color_name = "red"
            
            # Since each color has exactly 31 numbers, index % 31 + 1 gives the number
            # For the first pack, 0-30 = black, 31-61 = yellow, 62-92 = green
            number = (i % 31) + 1
            
            if color_name not in mapping:
                mapping[color_name] = {}
            mapping[color_name][number] = custom_emoji_id

    print("=== EMOJI ID MAPPING ===")
    print(mapping)
    return mapping

if __name__ == "__main__":
    if not BOT_TOKEN:
        print("❌ ОШИБКА: Пожалуйста, укажите BOT_TOKEN в переменных окружения!")
    else:
        fetch_ids()
