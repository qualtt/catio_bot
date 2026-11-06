"""Render the recovered publishing schedule as a local HTML page for manual review.

Reads posts straight from the target database (so it shows what the bot will actually see),
and joins the evidence and flags from reconstruction.json. Photos are referenced from
RECOVERY_DIR/photos, so the page works offline and never leaves this machine.

Usage:
    python -m bot.tools.recovery.schedule_report --database-url postgresql+asyncpg://catio_bot:recovery@127.0.0.1:5433/catio_bot_db
    open ~/catio_recovery/schedule.html
"""

from __future__ import annotations

import argparse
import asyncio
import html
import json
import os
from collections import defaultdict
from datetime import datetime

from bot.tools.recovery.common import RECONSTRUCTION_PATH, REPORT_PATH

WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def esc(value) -> str:
    return html.escape(str(value)) if value is not None else ""


async def load_posts():
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from db.database import async_session, engine
    from db.models.post import Post

    async with async_session() as session:
        posts = (await session.scalars(select(Post).options(selectinload(Post.user), selectinload(Post.photo)))).all()
    await engine.dispose()
    return posts


def render(posts, data: dict) -> str:
    from db.crud.time_utils import app_timezone

    tz = app_timezone()
    now = datetime.now(tz)
    bot_down_at = datetime.fromisoformat(data["bot_down_at"]).astimezone(tz)
    photo_paths = {p["sha256"]: p["path"] for p in data["photos"]}
    recon = {(p["photo_sha"], int(datetime.fromisoformat(p["created_at"]).timestamp())): p for p in data["posts"]}

    def info(post) -> dict:
        return recon.get((post.photo.sha256, int(post.created_at.timestamp())), {})

    slot_load: dict[datetime, int] = defaultdict(int)
    for p in posts:
        if p.status.value == "approved" and p.schedule_time:
            slot_load[p.schedule_time] += 1

    missed, queue, pending, doubtful, rejected, published = [], [], [], [], [], []
    for p in posts:
        status = p.status.value
        flags = info(p).get("flags", [])
        sched = p.schedule_time.astimezone(tz) if p.schedule_time else None
        if status == "approved":
            if sched is None or sched < bot_down_at:
                doubtful.append(p)
            elif sched < now:
                missed.append(p)
            else:
                queue.append(p)
        elif status == "pending":
            pending.append(p)
        elif status == "rejected":
            rejected.append(p)
        else:
            (doubtful if flags else published).append(p)

    def key(p):
        return (p.schedule_time or p.created_at, p.id)

    for group in (missed, queue, pending, doubtful, rejected):
        group.sort(key=key)
    published.sort(key=key, reverse=True)

    def card(p) -> str:
        i = info(p)
        user = p.user
        author = esc(user.full_name or "без имени")
        if user.username:
            author += f' <span class="muted">@{esc(user.username)}</span>'
        if p.schedule_time:
            local = p.schedule_time.astimezone(tz)
            sched = f"{WEEKDAYS[local.weekday()]} {local:%d.%m %H:%M}"
        else:
            sched = "время не выбрано"
        chips = [f'<span class="chip">{"авто" if p.is_auto_scheduled else "вручную"}</span>']
        if p.submission_group_size and p.submission_group_size > 1:
            chips.append(f'<span class="chip">альбом {p.submission_group_index}/{p.submission_group_size}</span>')
        if p.status.value == "approved" and p.schedule_time and slot_load[p.schedule_time] > 1:
            chips.append('<span class="chip warn">в этом слоте несколько постов</span>')
        flag_list = i.get("flags", [])
        recovered_time = i.get("schedule")
        if p.status.value == "approved" and p.schedule_time and recovered_time:
            current = p.schedule_time.astimezone(tz).strftime("%Y-%m-%d %H:%M")
            if current != recovered_time:
                was = datetime.strptime(recovered_time, "%Y-%m-%d %H:%M").replace(tzinfo=tz)
                chips.append(f'<span class="chip moved">перенесено с {was:%d.%m %H:%M}</span>')
                flag_list = [f for f in flag_list if not f.startswith("пропущено из-за сбоя")]
        flags = "".join(f'<li class="flag">{esc(f)}</li>' for f in flag_list)
        evidence = "".join(f"<li>{esc(e)}</li>" for e in i.get("evidence", []))
        path = photo_paths.get(p.photo.sha256, "")
        return f"""
<article class="card">
  <a class="thumb" href="{esc(path)}" target="_blank"><img loading="lazy" src="{esc(path)}" alt=""></a>
  <div class="meta">
    <div class="title"><span class="time">{sched}</span> · {esc(p.animal_type or "вид неизвестен")}</div>
    <div class="author">{author}</div>
    <div class="chips">{"".join(chips)}</div>
    {f'<ul class="flags">{flags}</ul>' if flags else ""}
    <details><summary>Почему так · пост #{p.id}</summary>
      <ul class="evidence">{evidence}</ul>
      <div class="ref">ref для правки: <code>{esc(i.get("ref", "?"))}</code></div>
    </details>
  </div>
</article>"""

    def grid(items) -> str:
        return (
            '<div class="grid">' + "".join(card(p) for p in items) + "</div>"
            if items
            else '<p class="muted">Пусто.</p>'
        )

    def section(title: str, note: str, content: str, count: int, open_: bool = True) -> str:
        return f"""
<details class="section" {"open" if open_ else ""}>
  <summary><h2>{title} <span class="count">{count}</span></h2></summary>
  <p class="note">{note}</p>
  {content}
</details>"""

    users_scores = "".join(
        f"<tr><td>{esc(u['full_name'] or '')} <span class='muted'>{'@' + esc(u['username']) if u['username'] else ''}</span></td>"
        f"<td class='num'>{u['score']}</td><td class='muted'>{esc(u['score_note'])}</td></tr>"
        for u in sorted(data["users"], key=lambda u: -u["score"])
    )

    stats = [
        ("в очереди", len(queue)),
        ("пропущено из-за сбоя", len(missed)),
        ("на модерации", len(pending)),
        ("под вопросом", len(doubtful)),
        ("отклонено", len(rejected)),
        ("опубликовано", len(published)),
    ]
    stat_html = "".join(f'<div class="stat"><b>{n}</b><span>{label}</span></div>' for label, n in stats)

    howto = section(
        "Как поправить",
        "",
        "<p class='note'>Создайте <code>~/catio_recovery/overrides.json</code> и укажите ref поста из «Почему так»:</p>"
        '<pre>{\n  "111111111:3470": {"schedule": "2026-11-20 11:00"},\n'
        '  "222222222:120": {"status": "rejected"},\n  "222222222:121": {"skip": true}\n}</pre>'
        "<p class='note'>Затем пересоберите: <code>reconstruct</code> → <code>build_db --replace</code> → "
        "<code>schedule_report</code>.</p>",
        0,
        open_=False,
    )

    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Расписание котиков</title>
<style>
:root {{ --bg:#f7f6f3; --surface:#fff; --text:#1d1d1f; --muted:#6e6e73; --line:#e4e2dd; --accent:#2f6f4f;
  --warn-bg:#fff4e0; --warn:#8a5300; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#161616; --surface:#202020; --text:#ececec; --muted:#9a9a9a;
  --line:#333; --accent:#7fc7a0; --warn-bg:#3a2a10; --warn:#f0b45c; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--text); font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
main {{ max-width:1180px; margin:0 auto; padding:24px 16px 64px; }}
h1 {{ font-size:26px; margin:0 0 4px; }} h2 {{ display:inline; font-size:20px; margin:0; }}
.muted {{ color:var(--muted); }} .note {{ color:var(--muted); margin:6px 0 12px; max-width:760px; }}
.stats {{ display:flex; flex-wrap:wrap; gap:10px; margin:18px 0 8px; }}
.stat {{ background:var(--surface); border:1px solid var(--line); border-radius:10px; padding:10px 14px; min-width:120px; }}
.stat b {{ display:block; font-size:22px; }} .stat span {{ color:var(--muted); font-size:13px; }}
.section {{ margin-top:28px; border-top:1px solid var(--line); padding-top:16px; }}
.section > summary {{ cursor:pointer; list-style:none; }} .section > summary::-webkit-details-marker {{ display:none; }}
.count {{ color:var(--muted); font-weight:400; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(min(220px,100%),1fr)); gap:14px; }}
main, .card, .meta {{ min-width:0; overflow-wrap:anywhere; }}
.card {{ background:var(--surface); border:1px solid var(--line); border-radius:12px; overflow:hidden; display:flex; flex-direction:column; }}
.thumb img {{ display:block; width:100%; aspect-ratio:4/3; object-fit:cover; background:var(--line); }}
.meta {{ padding:10px 12px 12px; display:flex; flex-direction:column; gap:6px; }}
.title {{ font-weight:600; }} .time {{ color:var(--accent); }}
.chips {{ display:flex; flex-wrap:wrap; gap:6px; }}
.chip {{ font-size:12px; border:1px solid var(--line); border-radius:999px; padding:1px 8px; color:var(--muted); }}

.chip.moved {{ color:var(--accent); border-color:var(--accent); }}
.chip.warn, .flag {{ background:var(--warn-bg); color:var(--warn); border-color:transparent; }}
.flags {{ list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:4px; }}
.flag {{ font-size:12.5px; border-radius:6px; padding:4px 8px; }}
details > summary {{ cursor:pointer; color:var(--muted); font-size:13px; }}
.evidence {{ margin:6px 0; padding-left:18px; font-size:12.5px; color:var(--muted); }}
.ref {{ font-size:12px; color:var(--muted); }} code {{ font-size:12px; }}
table {{ border-collapse:collapse; background:var(--surface); border:1px solid var(--line); border-radius:10px; }}
td {{ padding:6px 12px; border-top:1px solid var(--line); }} td.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
pre {{ background:var(--surface); border:1px solid var(--line); border-radius:8px; padding:10px; overflow-x:auto; font-size:12.5px; }}
</style></head>
<body><main>
<h1>Расписание публикаций после восстановления</h1>
<div class="muted">Бот перестал работать {bot_down_at:%d.%m.%Y %H:%M}. Страница собрана {now:%d.%m.%Y %H:%M} ({
        esc(data["timezone"])
    }) из новой БД.</div>
<div class="stats">{stat_html}</div>

{
        section(
            "Пропущено из-за сбоя",
            "Одобренные посты, время которых пришлось на простой бота. Публикатор берёт только посты не старше часа, "
            "поэтому сам он их уже не выложит: их нужно перенести на новые слоты или выложить вручную.",
            grid(missed),
            len(missed),
        )
    }
{section("Очередь публикаций", "Одобренные посты с будущим временем, по порядку выхода.", grid(queue), len(queue))}
{
        section(
            "На модерации",
            "Заявки, по которым не нашлось ни решения админа, ни уведомления автору.",
            grid(pending),
            len(pending),
        )
    }
{
        section(
            "Под вопросом",
            "Одобрены давно, но в канале не найдены; или бот сообщил автору о публикации, а в канале поста нет "
            "(возможно, удалён). Проверьте и при необходимости поправьте через overrides.json.",
            grid(doubtful),
            len(doubtful),
        )
    }
{section("Отклонённые", "", grid(rejected), len(rejected), open_=False)}
{
        section(
            "Опубликованные",
            "Совпали с постами в канале. Последние 60.",
            grid(published[:60]),
            len(published),
            open_=False,
        )
    }
{
        section(
            "Баллы пользователей",
            "Восстановлены приблизительно: последний показанный профиль плюс начисления после него. "
            "Баллы за определение старых фото и турниры после последнего профиля не учтены.",
            f"<table>{users_scores}</table>",
            len(data["users"]),
            open_=False,
        )
    }
{howto}
</main></body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database-url", required=True)
    args = parser.parse_args()
    os.environ["DATABASE_URL"] = args.database_url

    data = json.loads(RECONSTRUCTION_PATH.read_text())
    posts = asyncio.run(load_posts())
    REPORT_PATH.write_text(render(posts, data))
    print(f"written: {REPORT_PATH}")


if __name__ == "__main__":
    main()
