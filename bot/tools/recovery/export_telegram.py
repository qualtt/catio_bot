"""Read-only export of Catio photos and metadata from Telegram into a local folder.

Usage:
    python -m bot.tools.recovery.export_telegram channel   # channel history via the admin user session
    python -m bot.tools.recovery.export_telegram bot       # every message in the bot's private-chat box, by ID

Output (RECOVERY_DIR, default ~/catio_recovery):
    photos/<file_unique_id>.<ext>      one file per unique photo / image document
    manifest_<source>.jsonl            one JSON line per message (all messages, not only photos)

Nothing is written to Telegram, S3 or the database. Re-running resumes: messages already in
the manifest are skipped, and files already on disk are not downloaded again.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from pyrogram import Client, raw, types, utils
from pyrogram.errors import FloodWait, RPCError

from bot.tools.recovery.common import OUT_DIR, PHOTOS_DIR, env

ENV = env()
BOT_BATCH = 100
BOT_EMPTY_STOP = 1000  # stop the bot scan after this many consecutive empty IDs past the last real one

logging.basicConfig(level=logging.WARNING)
log = logging.getLogger("export")


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.astimezone()  # pyrogram returns naive local time
    return dt.astimezone(UTC).isoformat()


def user_dict(u: types.User | None) -> dict | None:
    if u is None:
        return None
    return {
        "id": u.id,
        "username": u.username,
        "first_name": u.first_name,
        "last_name": u.last_name,
        "is_bot": u.is_bot,
        "language_code": getattr(u, "language_code", None),
    }


def chat_dict(c: types.Chat | None) -> dict | None:
    if c is None:
        return None
    return {
        "id": c.id,
        "type": str(c.type.value) if c.type else None,
        "username": c.username,
        "title": c.title,
        "first_name": c.first_name,
        "last_name": c.last_name,
    }


def text_fields(s) -> dict | None:
    if not s:
        return None
    return {"text": str(s), "html": getattr(s, "html", None)}


def markup_dict(markup) -> list | None:
    if not isinstance(markup, types.InlineKeyboardMarkup):
        return None
    rows = []
    for row in markup.inline_keyboard:
        out_row = []
        for b in row:
            data = b.callback_data
            if isinstance(data, bytes):
                data = data.decode("utf-8", "replace")
            out_row.append({"text": b.text, "callback_data": data, "url": b.url})
        rows.append(out_row)
    return rows


def media_ref(msg: types.Message) -> tuple[object, str] | None:
    """Return (media object, file extension) for photos and image documents."""
    if msg.photo:
        return msg.photo, ".jpg"
    doc = msg.document
    if doc and (doc.mime_type or "").startswith("image/"):
        ext = Path(doc.file_name or "").suffix.lower() or "." + doc.mime_type.split("/", 1)[1]
        return doc, ext
    return None


def message_record(source: str, msg: types.Message) -> dict:
    rec = {
        "source": source,
        "message_id": msg.id,
        "date": iso(msg.date),
        "edit_date": iso(msg.edit_date),
        "chat": chat_dict(msg.chat),
        "from_user": user_dict(msg.from_user),
        "sender_chat": chat_dict(msg.sender_chat),
        "outgoing": bool(msg.outgoing),
        "service": str(msg.service.value) if msg.service else None,
        "media": str(msg.media.value) if msg.media else None,
        "media_group_id": msg.media_group_id,
        "text": text_fields(msg.text),
        "caption": text_fields(msg.caption),
        "reply_to_message_id": msg.reply_to_message_id,
        "forward_from": user_dict(msg.forward_from),
        "forward_from_chat": chat_dict(msg.forward_from_chat),
        "forward_from_message_id": msg.forward_from_message_id,
        "forward_date": iso(msg.forward_date),
        "reply_markup": markup_dict(msg.reply_markup),
        "file": None,
    }
    ref = media_ref(msg)
    if ref:
        media, ext = ref
        rec["file"] = {
            "kind": "photo" if msg.photo else "document",
            "file_unique_id": media.file_unique_id,
            "file_id": media.file_id,  # only valid for the session that fetched it
            "width": getattr(media, "width", None),
            "height": getattr(media, "height", None),
            "file_size": media.file_size,
            "mime_type": getattr(media, "mime_type", None),
            "path": f"photos/{media.file_unique_id}{ext}",
            "sha256": None,
        }
    return rec


async def with_flood(coro_factory):
    while True:
        try:
            return await coro_factory()
        except FloodWait as e:
            print(f"  flood wait {e.value}s", flush=True)
            await asyncio.sleep(e.value + 1)


async def download(app: Client, msg: types.Message, rec: dict) -> None:
    f = rec["file"]
    if not f:
        return
    target = OUT_DIR / f["path"]
    if not target.exists():
        tmp = target.with_suffix(target.suffix + f".part{os.getpid()}")
        for attempt in range(5):
            try:
                await with_flood(lambda: app.download_media(msg, file_name=str(tmp)))
                break
            except (OSError, TimeoutError, RPCError) as e:  # network hiccups
                print(f"  download retry {attempt + 1} for msg {msg.id}: {e!r}", flush=True)
                await asyncio.sleep(5 * (attempt + 1))
        if not tmp.exists():
            f["download_error"] = True
            return
        os.replace(tmp, target)
    f["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()


class Manifest:
    def __init__(self, source: str):
        self.path = OUT_DIR / f"manifest_{source}.jsonl"
        self.done: set[tuple[int | None, int]] = set()
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                r = json.loads(line)
                if not (r.get("file") or {}).get("download_error"):
                    self.done.add(((r.get("chat") or {}).get("id"), r["message_id"]))
        self.fh = self.path.open("a")

    def write(self, rec: dict) -> None:
        self.fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.fh.flush()


async def export_channel() -> None:
    channel = ENV["CHANNEL_ID"]
    manifest = Manifest("channel")
    app = Client(
        "export_user",
        api_id=int(ENV["API_ID"]),
        api_hash=ENV["API_HASH"],
        session_string=ENV["USERBOT_SESSION_STRING"],
        in_memory=True,
        no_updates=True,
    )
    n = photos = 0
    async with app:
        async for msg in app.get_chat_history(channel):
            n += 1
            if (msg.chat.id, msg.id) in manifest.done:
                continue
            rec = message_record("channel", msg)
            await download(app, msg, rec)
            photos += bool(rec["file"])
            manifest.write(rec)
            if n % 100 == 0:
                print(f"channel: {n} messages scanned, {photos} new files", flush=True)
    print(f"channel DONE: {n} messages scanned, {photos} new files", flush=True)


async def export_bot() -> None:
    manifest = Manifest("bot")
    app = Client(
        "export_bot",
        api_id=int(ENV["API_ID"]),
        api_hash=ENV["API_HASH"],
        bot_token=ENV["BOT_TOKEN"],
        in_memory=True,
        no_updates=True,
    )
    start = 1
    last_real = 0
    total = photos = 0
    async with app:
        while start - last_real <= BOT_EMPTY_STOP:
            ids = list(range(start, start + BOT_BATCH))
            r = await with_flood(
                lambda ids=ids: app.invoke(
                    raw.functions.messages.GetMessages(id=[raw.types.InputMessageID(id=i) for i in ids])
                )
            )
            msgs = await utils.parse_messages(app, r, replies=0)
            for msg in msgs:
                if msg.empty:
                    continue
                last_real = max(last_real, msg.id)
                total += 1
                if ((msg.chat.id if msg.chat else None), msg.id) in manifest.done:
                    continue
                rec = message_record("bot", msg)
                await download(app, msg, rec)
                photos += bool(rec["file"])
                manifest.write(rec)
            print(f"bot: IDs up to {start + BOT_BATCH - 1}, {total} messages, {photos} new files", flush=True)
            start += BOT_BATCH
            await asyncio.sleep(2)
    print(f"bot DONE: last message ID {last_real}, {total} messages, {photos} new files", flush=True)


def main() -> None:
    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "channel":
        asyncio.run(export_channel())
    elif mode == "bot":
        asyncio.run(export_bot())
    else:
        sys.exit("usage: export_telegram.py channel|bot")


if __name__ == "__main__":
    main()
