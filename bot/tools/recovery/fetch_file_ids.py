"""Collect Bot API–usable file ids for every exported photo.

The channel export runs under the admin's user session, so its file ids are useless to the bot.
This script re-reads the channel posts through the bot account (MTProto, read-only) to get file ids
that belong to the bot, then asks the Bot API (`getFile`, read-only) for each file's
`file_unique_id`. Pyrogram encodes `file_unique_id` differently from the Bot API, and the bot
compares new submissions against the Bot API value.

Usage:
    python -m bot.tools.recovery.fetch_file_ids

Output: file_ids.json in RECOVERY_DIR. Re-running only fetches what is missing.
"""

from __future__ import annotations

import asyncio
import json

import aiohttp
from pyrogram import Client
from pyrogram.errors import FloodWait

from bot.tools.recovery.common import FILE_IDS_PATH, MANIFEST_BOT, MANIFEST_CHANNEL, env, read_jsonl

ENV = env()


def load_cache() -> dict:
    if FILE_IDS_PATH.exists():
        return json.loads(FILE_IDS_PATH.read_text())
    return {"channel": {}, "bot_api_unique": {}}


def save_cache(cache: dict) -> None:
    tmp = FILE_IDS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=1))
    tmp.replace(FILE_IDS_PATH)


async def fetch_channel_file_ids(cache: dict) -> None:
    wanted = [
        r["message_id"]
        for r in read_jsonl(MANIFEST_CHANNEL)
        if r["file"] and str(r["message_id"]) not in cache["channel"]
    ]
    if not wanted:
        print("channel: all file ids cached")
        return

    app = Client(
        "recovery_file_ids",
        api_id=int(ENV["API_ID"]),
        api_hash=ENV["API_HASH"],
        bot_token=ENV["BOT_TOKEN"],
        in_memory=True,
        no_updates=True,
    )
    async with app:
        channel = await app.get_chat(ENV["CHANNEL_ID"])
        for start in range(0, len(wanted), 100):
            ids = wanted[start : start + 100]
            while True:
                try:
                    messages = await app.get_messages(channel.id, ids, replies=0)
                    break
                except FloodWait as e:
                    print(f"  flood wait {e.value}s", flush=True)
                    await asyncio.sleep(e.value + 1)
            for msg in messages:
                media = msg.photo or msg.document
                if msg.empty or media is None:
                    continue
                cache["channel"][str(msg.id)] = {"file_id": media.file_id, "pyrogram_unique_id": media.file_unique_id}
            save_cache(cache)
            print(f"channel: {min(start + 100, len(wanted))}/{len(wanted)}", flush=True)
            await asyncio.sleep(2)


async def fetch_bot_api_unique_ids(cache: dict) -> None:
    file_ids: dict[str, str] = {}  # pyrogram unique id (bot session) -> bot file_id
    for r in read_jsonl(MANIFEST_BOT):
        if r["file"] and not r["outgoing"]:
            file_ids.setdefault(r["file"]["file_unique_id"], r["file"]["file_id"])
    for item in cache["channel"].values():
        file_ids.setdefault(item["pyrogram_unique_id"], item["file_id"])

    todo = [(uid, fid) for uid, fid in file_ids.items() if uid not in cache["bot_api_unique"]]
    print(f"bot api getFile: {len(todo)} to fetch, {len(file_ids) - len(todo)} cached", flush=True)
    url = f"https://api.telegram.org/bot{ENV['BOT_TOKEN']}/getFile"
    sem = asyncio.Semaphore(8)
    done = 0

    async def one(session: aiohttp.ClientSession, uid: str, fid: str) -> None:
        nonlocal done
        async with sem:
            for _ in range(5):
                async with session.get(url, params={"file_id": fid}) as resp:
                    data = await resp.json()
                if data.get("ok"):
                    cache["bot_api_unique"][uid] = data["result"]["file_unique_id"]
                    break
                retry = (data.get("parameters") or {}).get("retry_after")
                if retry:
                    await asyncio.sleep(retry + 1)
                    continue
                print(f"  getFile failed for {uid}: {data.get('description')}", flush=True)
                break
            done += 1
            if done % 200 == 0:
                save_cache(cache)
                print(f"bot api getFile: {done}/{len(todo)}", flush=True)

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
        await asyncio.gather(*(one(session, uid, fid) for uid, fid in todo))
    save_cache(cache)


async def main() -> None:
    cache = load_cache()
    await fetch_channel_file_ids(cache)
    await fetch_bot_api_unique_ids(cache)
    print(
        f"DONE: channel file ids {len(cache['channel'])}, bot api unique ids {len(cache['bot_api_unique'])}",
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
