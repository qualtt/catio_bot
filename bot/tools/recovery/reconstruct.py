"""Rebuild users, photos, channel history and posts from the Telegram export.

Inputs (RECOVERY_DIR): manifest_bot.jsonl, manifest_channel.jsonl, photos/, file_ids.json,
optional overrides.json. Output: reconstruction.json. Nothing outside RECOVERY_DIR is touched.

How a post's status is decided, strongest evidence first:
1. The photo is in the channel (same sha256, or perceptual-hash distance <= 8)  -> published.
2. The final caption of the admin's copy of the submission ("✅ Одобрено. Публикация: …",
   "❌ Отклонено", the album card's "Статус: …")                                  -> that status.
3. Notifications the bot sent to the author ("Ваше фото (…) одобрено. Оно выйдет …",
   "… отклонено администратором", "… опубликовано в канале")                     -> that status.
4. Nothing after the submission                                                   -> pending.
Every post keeps the evidence it was decided from, and flags where sources disagree.

Manual corrections go to overrides.json, keyed by post ref ("<chat_id>:<message_id>"):
    {"123:456": {"status": "rejected", "schedule": "2026-11-20 11:00", "animal_type": "Кот", "skip": false}}

Usage:
    python -m bot.tools.recovery.reconstruct
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from itertools import pairwise
from zoneinfo import ZoneInfo

from bot.services.photo_storage import compute_perceptual_hash, hamming_distance
from bot.tools.recovery.common import (
    FILE_IDS_PATH,
    MANIFEST_BOT,
    MANIFEST_CHANNEL,
    OUT_DIR,
    OVERRIDES_PATH,
    RECONSTRUCTION_PATH,
    body,
    env,
    read_jsonl,
)

ENV = env()
TZ = ZoneInfo(ENV.get("TIMEZONE") or "Europe/Moscow")
PHASH_MAX_DISTANCE = int(ENV.get("DUPLICATE_PHASH_MAX_DISTANCE") or 8)
SCHED = r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2})"
SCHED_OR_NONE = r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}|не выбрано)"
STATUS_LABELS = {
    "на модерации": "pending",
    "запланировано": "approved",
    "отклонено": "rejected",
    "опубликовано": "published",
}

RE_SINGLE_CONFIRM = re.compile(r"^Фото отправлено на модерацию\. (Авторазмещение|Выбранное время): " + SCHED)
RE_ALBUM_CONFIRM = re.compile(r"^(✅ Альбом успешно отправлен|Альбом отправлен на модерацию)")
RE_ALBUM_LINE = re.compile(r"^(\d+)\. (.+?): " + SCHED + r"\s*$", re.MULTILINE)
RE_APPROVED_NOTIF = re.compile(
    r"^(Ваше фото|Фото из альбома) \((.+?)\) одобрено\. Оно выйдет " + SCHED + r"\. Баллы: \+(\d+)"
)
RE_REJECTED_NOTIF = re.compile(
    r"^(Ваше фото было|Фото из альбома было) отклонено администратором\.(?:\nПричина: (.*))?", re.DOTALL
)
RE_PUBLISHED_NOTIF = re.compile(r"^Ваше фото \((.+?)\) опубликовано в канале")
RE_PROFILE_SCORE = re.compile(r"^Баллы: (\d+)\s*(\n|$)")
RE_TOP_LINE = re.compile(r"^\d+\. (.+): (-?\d+)$", re.MULTILINE)
RESCALE_MARKER = "все баллы поделены на 10"

ADMIN_CAPTIONS = [
    (re.compile(r"^✅ Одобрено\. Публикация: " + SCHED), lambda m: {"status": "approved", "schedule": m[1]}),
    (re.compile(r"^❌ Отклонено(?:\nПричина: (.*))?", re.DOTALL), lambda m: {"status": "rejected", "reason": m[1]}),
    (re.compile(r"^Заявка \(ID: (\d+)\) одобрена"), lambda m: {"status": "approved", "old_post_id": int(m[1])}),
    (re.compile(r"^Заявка \(ID: (\d+)\) отклонена"), lambda m: {"status": "rejected", "old_post_id": int(m[1])}),
    (
        re.compile(r"^Новая предложка!\nВид: (.+)\nПубликация: " + SCHED_OR_NONE),
        lambda m: {"status": "pending", "animal_type": m[1], "schedule": m[2]},
    ),
    (
        re.compile(
            r"^Новая пачка предложки\n.*?Заявка /post_(\d+)\nВид: (.+)\nСтатус: (.+)\nПубликация: " + SCHED_OR_NONE,
            re.DOTALL,
        ),
        lambda m: {
            "status": STATUS_LABELS.get(m[3].strip()),
            "old_post_id": int(m[1]),
            "animal_type": m[2],
            "schedule": m[4],
        },
    ),
    (
        re.compile(r"^\d+\. Заявка #(\d+)\nВид: (.+)\nПубликация: " + SCHED_OR_NONE),
        lambda m: {"status": None, "old_post_id": int(m[1]), "animal_type": m[2], "schedule": m[3]},
    ),
    (re.compile(r"^Заявка #(\d+)"), lambda m: {"status": "pending", "old_post_id": int(m[1])}),
    (re.compile(r"^Новая заявка \(ID: (\d+)\):"), lambda m: {"status": "pending", "old_post_id": int(m[1])}),
]
RE_ADMIN_KB = re.compile(r"^(?:admin_)?(?:approve|reject|change)_(\d+)$")


def parse_schedule(value: str | None) -> datetime | None:
    if not value or value == "не выбрано":
        return None
    return datetime.strptime(value, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)


def fmt(dt: datetime | None) -> str | None:
    return dt.astimezone(TZ).strftime("%Y-%m-%d %H:%M") if dt else None


def msg_date(record: dict) -> datetime:
    return datetime.fromisoformat(record["date"])


@dataclass
class Post:
    ref: str
    chat_id: int
    user_telegram_id: int
    user_message_id: int
    photo_uid: str  # pyrogram file_unique_id in the bot session
    photo_sha: str
    photo_path: str
    file_id: str
    created_at: str
    kind: str  # single | album
    confirm_message_id: int
    requested_schedule: str | None
    is_auto: bool
    group_ref: str | None = None
    group_index: int | None = None
    group_size: int | None = None
    animal_type: str | None = None
    old_post_id: int | None = None
    status: str | None = None
    schedule: str | None = None
    channel_message_id: int | None = None
    channel_distance: int | None = None
    evidence: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    # internal
    admin: dict | None = None
    notif_approved: dict | None = None
    notif_rejected: dict | None = None
    notif_published: dict | None = None


class PhashCache:
    def __init__(self) -> None:
        self.path = OUT_DIR / "phash_cache.json"
        self.data: dict[str, str | None] = json.loads(self.path.read_text()) if self.path.exists() else {}

    def get(self, rel_path: str) -> str | None:
        if rel_path not in self.data:
            self.data[rel_path] = compute_perceptual_hash((OUT_DIR / rel_path).read_bytes())
        return self.data[rel_path]

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data))


def classify_admin_caption(record: dict) -> dict | None:
    text = body(record)
    for pattern, build in ADMIN_CAPTIONS:
        m = pattern.match(text)
        if m:
            info = build(m)
            for row in record.get("reply_markup") or []:
                for button in row:
                    km = RE_ADMIN_KB.match(button.get("callback_data") or "")
                    if km:
                        info.setdefault("old_post_id", int(km[1]))
            info["message_id"] = record["message_id"]
            info["text"] = text.split("\n\n")[0].replace("\n", " · ")
            return info
    return None


def build_users(bot: list[dict]) -> dict[int, dict]:
    users: dict[int, dict] = {}
    for r in bot:
        u = r.get("from_user")
        if r["outgoing"] or not u or u.get("is_bot"):
            continue
        full_name = " ".join(x for x in (u.get("first_name"), u.get("last_name")) if x) or None
        users[u["id"]] = {"telegram_id": u["id"], "username": u.get("username"), "full_name": full_name}

    # Score = the latest exact value the bot ever showed for the user (a /top leaderboard
    # or their /profile), plus approval points awarded after it. On 2026-07-25 every score
    # was divided by 10 ("все баллы поделены на 10"), so older values are rescaled.
    # Identification points only came from admin batch reviews, which ended in July.
    def when(r: dict) -> datetime:
        return datetime.fromisoformat(r["edit_date"] or r["date"])

    snapshots: list[tuple[datetime, dict[str, int]]] = []
    by_chat: dict[int, list[dict]] = defaultdict(list)
    for r in bot:
        if not r["outgoing"]:
            continue
        by_chat[r["chat"]["id"]].append(r)
        if body(r).startswith("Топ участников"):
            lines = {name: int(score) for name, score in RE_TOP_LINE.findall(body(r))}
            if lines:
                snapshots.append((when(r), lines))

    # The rescale happened before it was announced, and was a recalculation rather than an exact
    # division, so take its moment from the data: the first leaderboard whose top score is less
    # than half of the previous one's.
    snapshots.sort(key=lambda s: s[0])
    rescale_at = min((msg_date(r) for r in bot if r["outgoing"] and RESCALE_MARKER in body(r)), default=None)
    for (_, prev), (t, cur) in pairwise(snapshots):
        if 0 < max(cur.values()) * 2 < max(prev.values()):
            rescale_at = t
            break

    for tid, user in users.items():
        names = {n for n in (user["username"], user["full_name"], str(tid)) if n}
        candidates = [(t, s[n], f"рейтинг /top от {t:%d.%m.%Y}") for t, s in snapshots for n in names if n in s]
        for r in by_chat.get(tid, []):
            m = RE_PROFILE_SCORE.match(body(r))
            if m:
                candidates.append((when(r), int(m[1]), f"профиль от {when(r):%d.%m.%Y}"))
        base_at, base, note = max(candidates, default=(datetime(2000, 1, 1, tzinfo=TZ), 0, "сумма начислений"))

        before_rescale = after_rescale = 0
        for r in by_chat.get(tid, []):
            m = RE_APPROVED_NOTIF.match(body(r))
            if m and msg_date(r) > base_at:
                if rescale_at and msg_date(r) < rescale_at:
                    before_rescale += int(m[4])
                else:
                    after_rescale += int(m[4])
        if rescale_at and base_at < rescale_at:
            score = (base + before_rescale) // 10 + after_rescale
            note += ", до пересчёта 25.07 — разделено на 10 приблизительно"
        else:
            score = base + before_rescale + after_rescale
        if after_rescale or before_rescale:
            note += " + одобрения после него"
        user["score"] = score
        user["score_note"] = note
    return users


def build_posts(bot: list[dict], admin_id: int) -> list[Post]:
    by_chat: dict[int, list[dict]] = defaultdict(list)
    for r in bot:
        if r["chat"] and r["chat"]["type"] == "private":
            by_chat[r["chat"]["id"]].append(r)

    posts: list[Post] = []
    for chat_id, msgs in by_chat.items():
        msgs.sort(key=lambda r: r["message_id"])
        incoming = [r for r in msgs if not r["outgoing"] and r["file"] and r["from_user"]]
        assigned: set[int] = set()

        def unassigned_before(
            message_id: int, count: int, incoming: list[dict] = incoming, assigned: set[int] = assigned
        ) -> list[dict]:
            pool = [r for r in incoming if r["message_id"] < message_id and r["message_id"] not in assigned]
            return pool[-count:] if len(pool) >= count else []

        def new_post(
            photo: dict,
            confirm: dict,
            kind: str,
            requested: str | None,
            is_auto: bool,
            chat_id: int = chat_id,
            assigned: set[int] = assigned,
        ) -> Post:
            assigned.add(photo["message_id"])
            return Post(
                ref=f"{chat_id}:{photo['message_id']}",
                chat_id=chat_id,
                user_telegram_id=photo["from_user"]["id"],
                user_message_id=photo["message_id"],
                photo_uid=photo["file"]["file_unique_id"],
                photo_sha=photo["file"]["sha256"],
                photo_path=photo["file"]["path"],
                file_id=photo["file"]["file_id"],
                created_at=confirm["date"],
                kind=kind,
                confirm_message_id=confirm["message_id"],
                requested_schedule=requested,
                is_auto=is_auto,
            )

        for r in msgs:
            if not r["outgoing"]:
                continue
            text = body(r)
            m = RE_SINGLE_CONFIRM.match(text)
            if m:
                photos = unassigned_before(r["message_id"], 1)
                if not photos:
                    continue
                post = new_post(photos[0], r, "single", m[2], m[1] == "Авторазмещение")
                post.evidence.append(f"отправлено {r['date'][:16]} UTC: «{text.splitlines()[0]}»")
                posts.append(post)
                continue
            if RE_ALBUM_CONFIRM.match(text):
                tail = re.split(r"Расписание:|займут слоты:", text)[-1]
                lines = RE_ALBUM_LINE.findall(tail)
                photos = unassigned_before(r["message_id"], len(lines))
                if not lines or not photos:
                    continue
                group_ref = f"recovered-{chat_id}-{r['message_id']}"
                for index, (photo, (_, animal, sched)) in enumerate(zip(photos, lines, strict=True), start=1):
                    post = new_post(photo, r, "album", sched, "Автоматически" in text)
                    post.group_ref, post.group_index, post.group_size = group_ref, index, len(lines)
                    post.animal_type = animal
                    post.evidence.append(f"альбом {r['date'][:16]} UTC, фото {index}/{len(lines)}: {animal}, {sched}")
                    posts.append(post)
    return posts


def attach_admin_evidence(posts: list[Post], bot: list[dict], admin_id: int) -> None:
    by_uid: dict[str, list[dict]] = defaultdict(list)
    for r in bot:
        if r["outgoing"] and r["file"] and r["chat"]["id"] == admin_id:
            info = classify_admin_caption(r)
            if info:
                by_uid[r["file"]["file_unique_id"]].append(info)
    decisive = {"approved": 2, "rejected": 2, "published": 2, "pending": 1, None: 0}
    by_photo: dict[str, list[Post]] = defaultdict(list)
    for post in posts:
        by_photo[post.photo_uid].append(post)
    for post in posts:
        # The same photo can be submitted more than once; a card belongs to the latest
        # submission of that photo made before the card was sent.
        later = [
            p.confirm_message_id for p in by_photo[post.photo_uid] if p.confirm_message_id > post.confirm_message_id
        ]
        until = min(later, default=float("inf"))
        cards = [c for c in by_uid.get(post.photo_uid, []) if post.confirm_message_id < c["message_id"] < until]
        if not cards:
            continue
        card = max(cards, key=lambda c: (decisive[c["status"]], c["message_id"]))
        post.admin = card
        post.old_post_id = card.get("old_post_id") or post.old_post_id
        post.animal_type = post.animal_type or card.get("animal_type")
        post.evidence.append(f"карточка у админа: «{card['text']}»")


def attach_notifications(posts: list[Post], bot: list[dict]) -> None:
    by_chat: dict[int, list[Post]] = defaultdict(list)
    for post in posts:
        by_chat[post.chat_id].append(post)

    for r in sorted(bot, key=lambda r: r["message_id"]):
        if not r["outgoing"] or r["chat"]["id"] not in by_chat:
            continue
        text = body(r)
        candidates = [p for p in by_chat[r["chat"]["id"]] if p.confirm_message_id <= r["message_id"]]

        m = RE_APPROVED_NOTIF.match(text)
        if m:
            kind = "album" if m[1] == "Фото из альбома" else "single"
            pool = [p for p in candidates if p.kind == kind and p.notif_approved is None and p.notif_rejected is None]
            pool.sort(
                key=lambda p: (
                    p.requested_schedule != m[3] and (p.admin or {}).get("schedule") != m[3],
                    p.animal_type not in (None, m[2]),
                    p.confirm_message_id,
                    p.group_index or 0,
                )
            )
            if pool:
                pool[0].notif_approved = {"animal_type": m[2], "schedule": m[3], "points": int(m[4]), "date": r["date"]}
                pool[0].evidence.append(f"автору {r['date'][:16]} UTC: «{text.splitlines()[0]}»")
            continue

        m = RE_REJECTED_NOTIF.match(text)
        if m:
            kind = "album" if m[1].startswith("Фото из альбома") else "single"
            pool = [
                p
                for p in candidates
                if p.kind == kind
                and p.notif_approved is None
                and p.notif_rejected is None
                and (p.admin or {}).get("status") != "approved"
            ]
            pool.sort(
                key=lambda p: ((p.admin or {}).get("status") != "rejected", p.confirm_message_id, p.group_index or 0)
            )
            if pool:
                pool[0].notif_rejected = {"reason": (m[2] or "").strip() or None, "date": r["date"]}
                pool[0].evidence.append(f"автору {r['date'][:16]} UTC: «{text.replace(chr(10), ' · ')[:120]}»")
            continue

        m = RE_PUBLISHED_NOTIF.match(text)
        if m:
            sent = msg_date(r)
            pool = [
                p
                for p in candidates
                if p.notif_published is None
                and (p.notif_approved or (p.admin or {}).get("status") == "approved")
                and p.animal_type in (None, m[1])
            ]

            def distance(p: Post, sent: datetime = sent) -> float:
                sched = parse_schedule((p.notif_approved or {}).get("schedule") or (p.admin or {}).get("schedule"))
                return abs((sent - sched).total_seconds()) if sched else 1e12

            pool.sort(key=distance)
            if pool and distance(pool[0]) < 6 * 3600:
                pool[0].notif_published = {"date": r["date"]}
                pool[0].evidence.append(f"автору {r['date'][:16]} UTC: «{text}»")


def match_channel(posts: list[Post], channel: list[dict], phashes: PhashCache) -> dict[int, Post]:
    channel_photos = [r for r in channel if r["file"]]
    by_sha: dict[str, list[dict]] = defaultdict(list)
    for r in channel_photos:
        by_sha[r["file"]["sha256"]].append(r)
    hashes = {r["message_id"]: phashes.get(r["file"]["path"]) for r in channel_photos}

    used: dict[int, Post] = {}
    for post in sorted(posts, key=lambda p: (p.created_at, p.group_index or 0)):
        created = datetime.fromisoformat(post.created_at)
        exact = [r for r in by_sha.get(post.photo_sha, []) if r["message_id"] not in used and msg_date(r) >= created]
        if exact:
            best, dist = exact[0], 0
        else:
            ph = phashes.get(post.photo_path)
            scored = [
                (hamming_distance(ph, hashes[r["message_id"]]), r)
                for r in channel_photos
                if r["message_id"] not in used and msg_date(r) >= created
            ]
            scored = [(d, r) for d, r in scored if d is not None and d <= PHASH_MAX_DISTANCE]
            if not scored:
                continue
            dist, best = min(scored, key=lambda x: (x[0], x[1]["message_id"]))
        used[best["message_id"]] = post
        post.channel_message_id = best["message_id"]
        post.channel_distance = dist
        how = "то же фото" if dist == 0 else f"похожее фото, pHash-дистанция {dist}"
        post.evidence.append(f"в канале: пост {best['message_id']} от {fmt(msg_date(best))} ({how})")
    return used


def resolve(post: Post, bot_down_at: datetime, now: datetime) -> None:
    admin = post.admin or {}
    approved_sched = (post.notif_approved or {}).get("schedule") or (
        admin.get("schedule") if admin.get("status") == "approved" else None
    )
    post.animal_type = post.animal_type or (post.notif_approved or {}).get("animal_type")

    if admin.get("status") == "approved" and post.notif_rejected:
        post.flags.append("карточка админа: одобрено, но автору ушло «отклонено»")
    if admin.get("status") == "rejected" and post.notif_approved:
        post.flags.append("карточка админа: отклонено, но автору ушло «одобрено»")
    if (
        post.notif_approved
        and admin.get("status") == "approved"
        and admin.get("schedule")
        and admin["schedule"] != post.notif_approved["schedule"]
    ):
        post.flags.append(f"время расходится: у админа {admin['schedule']}, автору {post.notif_approved['schedule']}")

    if post.channel_message_id is not None:
        post.status = "published"
        if post.channel_distance and post.channel_distance > 4:
            post.flags.append(f"совпадение с каналом неточное (pHash {post.channel_distance}), проверьте")
    elif post.notif_published:
        post.status = "published"
        post.schedule = fmt(datetime.fromisoformat(post.notif_published["date"]))
        post.flags.append("автору ушло «опубликовано», но в канале фото не найдено (возможно, пост удалён)")
    elif approved_sched or admin.get("status") == "approved":
        post.status = "approved"
        post.schedule = approved_sched or post.requested_schedule
    elif admin.get("status") == "rejected" or post.notif_rejected:
        post.status = "rejected"
    elif admin.get("status") in ("pending", "published"):
        post.status = admin["status"]
        post.schedule = admin.get("schedule") if admin.get("schedule") != "не выбрано" else None
    else:
        post.status = "pending"

    if post.status == "published":
        # Disagreements about moderation only matter for posts that are still queued.
        post.flags = [f for f in post.flags if not f.startswith(("время расходится", "карточка админа:"))]

    if post.status == "pending":
        post.schedule = post.schedule or post.requested_schedule
        if not post.admin:
            post.flags.append("карточка у админа не найдена")

    if post.status == "approved":
        sched = parse_schedule(post.schedule)
        if sched is None:
            post.flags.append("одобрено, но время публикации неизвестно")
        elif sched < bot_down_at:
            post.flags.append("должно было выйти до сбоя, но в канале не найдено")
        elif sched < now:
            post.flags.append("пропущено из-за сбоя: время прошло, бот это уже не опубликует")


def apply_overrides(posts: list[Post]) -> list[Post]:
    if not OVERRIDES_PATH.exists():
        return posts
    overrides = json.loads(OVERRIDES_PATH.read_text())
    kept = []
    for post in posts:
        o = overrides.get(post.ref)
        if not o:
            kept.append(post)
            continue
        if o.get("skip"):
            continue
        for key, attr in (("status", "status"), ("schedule", "schedule"), ("animal_type", "animal_type")):
            if key in o:
                setattr(post, attr, o[key])
        post.flags = [f for f in post.flags if not f.startswith(("пропущено", "должно было", "одобрено, но"))]
        post.evidence.append(f"ручная правка: {json.dumps(o, ensure_ascii=False)}")
        kept.append(post)
    return kept


def main() -> None:
    admin_id = int(ENV["ADMIN_ID"])
    bot = sorted(read_jsonl(MANIFEST_BOT), key=lambda r: r["message_id"])
    channel = sorted(read_jsonl(MANIFEST_CHANNEL), key=lambda r: r["message_id"])
    file_ids = (
        json.loads(FILE_IDS_PATH.read_text()) if FILE_IDS_PATH.exists() else {"channel": {}, "bot_api_unique": {}}
    )
    phashes = PhashCache()

    bot_down_at = max(msg_date(r) for r in bot if r["outgoing"])
    now = datetime.now(TZ)

    users = build_users(bot)
    posts = build_posts(bot, admin_id)
    attach_admin_evidence(posts, bot, admin_id)
    attach_notifications(posts, bot)
    channel_to_post = match_channel(posts, channel, phashes)
    for post in posts:
        resolve(post, bot_down_at, now)
    posts = apply_overrides(posts)
    channel_to_post = {mid: p for mid, p in channel_to_post.items() if p in posts and p.status == "published"}

    photos: dict[str, dict] = {}

    def add_photo(sha: str, path: str, source: str, uid: str | None, file_id: str | None) -> None:
        if sha in photos:
            return
        full = OUT_DIR / path
        photos[sha] = {
            "sha256": sha,
            "path": path,
            "source": source,
            "file_size": full.stat().st_size,
            "content_type": "image/jpeg" if path.endswith(".jpg") else f"image/{path.rsplit('.', 1)[-1]}",
            "perceptual_hash": phashes.get(path),
            "telegram_file_id": file_id,
            "telegram_file_unique_id": file_ids["bot_api_unique"].get(uid) if uid else None,
        }

    for post in posts:
        add_photo(post.photo_sha, post.photo_path, "submissions", post.photo_uid, post.file_id)

    channel_history = []
    missing_file_ids = 0
    for r in channel:
        if not r["file"]:
            continue
        cached = file_ids["channel"].get(str(r["message_id"]))
        if not cached:
            missing_file_ids += 1
            continue
        post = channel_to_post.get(r["message_id"])
        if post:
            photo_sha = post.photo_sha
        else:
            photo_sha = r["file"]["sha256"]
            add_photo(photo_sha, r["file"]["path"], "channel-history", cached["pyrogram_unique_id"], cached["file_id"])
        channel_history.append(
            {
                "chat_id": r["chat"]["id"],
                "message_id": r["message_id"],
                "photo_sha": photo_sha,
                "file_id": cached["file_id"],
                "published_at": r["date"],
                "caption": body(r) or None,
                "media_group_id": r["media_group_id"],
                "animal_type": post.animal_type if post else None,
            }
        )
    phashes.save()

    post_dicts = []
    for p in sorted(posts, key=lambda p: (p.created_at, p.group_index or 0)):
        d = asdict(p)
        for key in ("admin", "notif_approved", "notif_rejected", "notif_published"):
            d.pop(key)
        if p.status == "published" and p.channel_message_id is not None:
            d["schedule"] = fmt(msg_date(next(r for r in channel if r["message_id"] == p.channel_message_id)))
        post_dicts.append(d)

    result = {
        "generated_at": datetime.now(TZ).isoformat(),
        "timezone": str(TZ),
        "bot_down_at": bot_down_at.isoformat(),
        "users": sorted(users.values(), key=lambda u: u["telegram_id"]),
        "photos": list(photos.values()),
        "channel_history": channel_history,
        "posts": post_dicts,
    }
    RECONSTRUCTION_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=1))

    by_status: dict[str, int] = defaultdict(int)
    for p in post_dicts:
        by_status[p["status"]] += 1
    print(f"users: {len(users)} | photos: {len(photos)} | channel_history: {len(channel_history)}")
    print(f"posts: {len(post_dicts)} {dict(by_status)} | with flags: {sum(bool(p['flags']) for p in post_dicts)}")
    if missing_file_ids:
        print(f"WARNING: {missing_file_ids} channel photos have no bot file id yet (run fetch_file_ids)")
    print(f"written: {RECONSTRUCTION_PATH}")


if __name__ == "__main__":
    main()
