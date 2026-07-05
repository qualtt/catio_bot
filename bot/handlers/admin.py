from datetime import date, datetime, time

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InputMediaPhoto, Message
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from bot.config import config
from bot.content import bot_content
from bot.keyboards.inline import (
    get_admin_album_kb,
    get_admin_album_view_kb,
    get_admin_animal_change_kb,
    get_admin_approval_kb,
    get_admin_menu_kb,
    get_admin_post_manage_kb,
    get_admin_rejection_reason_kb,
    get_admin_reschedule_cancel_kb,
    get_admin_schedule_kb,
)
from bot.services.captions import admin_album_control_text, admin_album_view_caption, format_schedule, submission_caption
from bot.services.publisher import publish_post
from bot.services.scoring import award_post_approval_score
from db.crud import app_timezone, combine_slot, ensure_animal_type, get_animal_type_name, get_next_auto_slot, now_in_app_tz
from db.database import async_session
from db.models.post import Post, PostStatus

admin_router = Router()


class AdminState(StatesGroup):
    waiting_for_reschedule_time = State()
    waiting_for_rejection_reason = State()


def is_admin(callback: CallbackQuery) -> bool:
    return callback.from_user.id == config.ADMIN_ID


def post_author(post: Post) -> str:
    if post.user and post.user.username:
        return f"@{post.user.username}"
    if post.user:
        return str(post.user.telegram_id)
    return bot_content.message("author_unknown")


def admin_post_caption(post: Post) -> str:
    return submission_caption(
        animal_type=post.animal_type,
        schedule=format_schedule(post.schedule_time),
        author=post_author(post),
        duplicate_of_photo_id=post.duplicate_of_photo_id,
        duplicate_distance=post.duplicate_distance,
    )


def parse_schedule_date(raw_value: str | None) -> date:
    if raw_value == "today" or not raw_value:
        return now_in_app_tz().date()
    return date.fromisoformat(raw_value)


def parse_admin_datetime(raw_value: str) -> datetime | None:
    value = " ".join(raw_value.split())
    for date_time_format in ("%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M"):
        try:
            parsed = datetime.strptime(value, date_time_format)
        except ValueError:
            continue
        return parsed.replace(tzinfo=app_timezone())
    return None


def admin_schedule_text(target_date: date, posts: list[Post]) -> str:
    if not posts:
        return bot_content.message("admin_schedule_empty", date=target_date.isoformat())

    lines = []
    for post in posts:
        lines.append(
            bot_content.message(
                "admin_schedule_line",
                post_id=post.id,
                time=format_schedule(post.schedule_time)[11:16],
                animal_type=post.animal_type,
                author=post_author(post),
            )
        )
    return bot_content.message(
        "admin_schedule_header",
        date=target_date.isoformat(),
        count=len(posts),
        posts="\n".join(lines),
    )


def admin_post_manage_text(post: Post) -> str:
    return bot_content.message(
        "admin_post_manage",
        post_id=post.id,
        animal_type=post.animal_type,
        status=bot_content.status_label(post.status),
        schedule=format_schedule(post.schedule_time),
        author=post_author(post),
    )


def is_album_post(post: Post) -> bool:
    return bool(post.submission_group_id)


def normalize_rejection_reason(value: str | None) -> str | None:
    reason = " ".join((value or "").split())
    return reason[:500].rstrip() or None


def duplicate_rejection_reason(post: Post) -> str | None:
    if post.duplicate_of_photo_id is None:
        return None

    if post.duplicate_distance == 0:
        return bot_content.message("duplicate_exact_rejection_reason", photo_id=post.duplicate_of_photo_id)

    distance = post.duplicate_distance if post.duplicate_distance is not None else "unknown"
    return bot_content.message(
        "duplicate_similar_rejection_reason",
        photo_id=post.duplicate_of_photo_id,
        distance=distance,
    )


def normalize_duplicate_rejection_reason(value: str | None, post: Post) -> str | None:
    reason = normalize_rejection_reason(value)
    if reason is None:
        return None

    reason_words = set(reason.casefold().replace("ё", "е").split())
    if reason_words & {"копия", "дубль", "дубликат", "повтор"}:
        return duplicate_rejection_reason(post) or reason

    return reason


def approved_user_notification_text(post: Post, *, schedule: str, points: int) -> str:
    if is_album_post(post):
        return bot_content.message(
            "approved_album_user_notification",
            animal_type=post.animal_type,
            schedule=schedule,
            points=points,
        )
    return bot_content.message(
        "approved_user_notification",
        animal_type=post.animal_type,
        schedule=schedule,
        points=points,
    )


def approved_callback_text(post: Post, *, points: int) -> str:
    if is_album_post(post):
        return bot_content.message("approved_album_callback", points=points)
    return bot_content.message("approved_callback", points=points)


def rejected_admin_caption(reason: str | None = None) -> str:
    if reason:
        return bot_content.message("rejected_caption_with_reason", reason=reason)
    return bot_content.message("rejected_caption")


def rejected_user_notification_text(post: Post, *, reason: str | None = None) -> str:
    if is_album_post(post):
        if reason:
            return bot_content.message("rejected_album_user_notification_with_reason", reason=reason)
        return bot_content.message("rejected_album_user_notification")

    if reason:
        return bot_content.message("rejected_user_notification_with_reason", reason=reason)
    return bot_content.message("rejected_user_notification")


async def load_post(session, post_id: int) -> Post | None:
    stmt = select(Post).options(selectinload(Post.user), selectinload(Post.photo)).where(Post.id == post_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def lock_post(session, post_id: int) -> Post | None:
    stmt = (
        select(Post)
        .options(selectinload(Post.user), selectinload(Post.photo))
        .where(Post.id == post_id)
        .with_for_update()
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def load_admin_schedule_posts(session, target_date: date) -> list[Post]:
    day_start = combine_slot(target_date, time.min)
    day_end = combine_slot(target_date, time.max)
    stmt = (
        select(Post)
        .options(selectinload(Post.user))
        .where(
            Post.status == PostStatus.APPROVED,
            Post.schedule_time >= day_start,
            Post.schedule_time <= day_end,
        )
        .order_by(Post.schedule_time.asc(), Post.id.asc())
    )
    return list((await session.execute(stmt)).scalars())


async def load_admin_stats(session) -> str:
    status_rows = await session.execute(select(Post.status, func.count(Post.id)).group_by(Post.status))
    status_counts = dict(status_rows.all())
    now = now_in_app_tz()
    today = now.date()
    today_start = combine_slot(today, time.min)
    today_end = combine_slot(today, time.max)

    today_scheduled = await session.scalar(
        select(func.count(Post.id)).where(
            Post.status == PostStatus.APPROVED,
            Post.schedule_time >= today_start,
            Post.schedule_time <= today_end,
        )
    )
    overdue = await session.scalar(
        select(func.count(Post.id)).where(
            Post.status == PostStatus.APPROVED,
            Post.schedule_time < now,
        )
    )
    next_post = (
        await session.execute(
            select(Post)
            .where(Post.status == PostStatus.APPROVED)
            .order_by(Post.schedule_time.asc(), Post.id.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    animal_rows = await session.execute(
        select(Post.animal_type, func.count(Post.id))
        .where(Post.status.in_([PostStatus.APPROVED, PostStatus.PUBLISHED]))
        .group_by(Post.animal_type)
        .order_by(func.count(Post.id).desc(), Post.animal_type.asc())
        .limit(5)
    )
    animal_stats = "\n".join(
        bot_content.message("admin_stats_animal_line", animal_type=animal_type or "?", count=count)
        for animal_type, count in animal_rows.all()
    ) or bot_content.message("admin_stats_no_animals")

    return bot_content.message(
        "admin_stats_text",
        pending=status_counts.get(PostStatus.PENDING, 0),
        approved=status_counts.get(PostStatus.APPROVED, 0),
        rejected=status_counts.get(PostStatus.REJECTED, 0),
        published=status_counts.get(PostStatus.PUBLISHED, 0),
        today_scheduled=today_scheduled or 0,
        overdue=overdue or 0,
        next_schedule=format_schedule(next_post.schedule_time) if next_post else bot_content.message("schedule_not_selected"),
        animal_stats=animal_stats,
    )


async def load_submission_group_posts(session, post: Post) -> list[Post]:
    if not post.submission_group_id:
        return [post]

    stmt = (
        select(Post)
        .options(selectinload(Post.user))
        .where(Post.submission_group_id == post.submission_group_id)
        .order_by(Post.submission_group_index.asc(), Post.id.asc())
    )
    return list((await session.execute(stmt)).scalars())


async def edit_admin_album_view_message(
    target,
    session,
    post: Post,
    *,
    use_media: bool = True,
) -> None:
    posts = await load_submission_group_posts(session, post)
    caption = admin_album_view_caption(posts, post, author=post_author(post))
    reply_markup = get_admin_album_view_kb(posts, post)

    if use_media:
        await target.edit_media(
            media=InputMediaPhoto(media=post.file_id, caption=caption),
            reply_markup=reply_markup,
        )
        return

    await target.edit_caption(caption=caption, reply_markup=reply_markup)


async def refresh_admin_album_control(callback: CallbackQuery, session, post: Post) -> None:
    posts = await load_submission_group_posts(session, post)
    if callback.message.text:
        await callback.message.edit_text(
            admin_album_control_text(posts, author=post_author(post)),
            reply_markup=get_admin_album_kb(posts),
        )
        return

    await edit_admin_album_view_message(callback.message, session, post, use_media=True)


def callback_is_album_control(callback: CallbackQuery, post: Post) -> bool:
    return bool(post.submission_group_id and callback.message and (callback.message.text or callback.message.photo))


async def edit_admin_rejection_result(
    bot: Bot,
    session,
    post: Post,
    *,
    chat_id: int,
    message_id: int,
    is_album_control: bool,
    is_album_view: bool = False,
    reason: str | None = None,
) -> None:
    if is_album_control:
        posts = await load_submission_group_posts(session, post)
        if is_album_view:
            await bot.edit_message_media(
                chat_id=chat_id,
                message_id=message_id,
                media=InputMediaPhoto(
                    media=post.file_id,
                    caption=admin_album_view_caption(posts, post, author=post_author(post)),
                ),
                reply_markup=get_admin_album_view_kb(posts, post),
            )
            return

        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=admin_album_control_text(posts, author=post_author(post)),
            reply_markup=get_admin_album_kb(posts),
        )
        return

    await bot.edit_message_caption(
        chat_id=chat_id,
        message_id=message_id,
        caption=rejected_admin_caption(reason),
    )


async def reject_post(session, post: Post, *, reason: str | None = None) -> None:
    post.status = PostStatus.REJECTED
    await session.commit()


async def notify_rejected_post_user(bot: Bot, post: Post, *, reason: str | None = None) -> None:
    if not post.user:
        return

    try:
        await bot.send_message(
            chat_id=post.user.telegram_id,
            text=rejected_user_notification_text(post, reason=reason),
        )
    except TelegramAPIError:
        pass


async def send_admin_schedule(target, target_date: date, *, callback_text: str | None = None) -> None:
    async with async_session() as session:
        posts = await load_admin_schedule_posts(session, target_date)

    text = admin_schedule_text(target_date, posts)
    reply_markup = get_admin_schedule_kb(target_date, posts)
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=reply_markup)
        await target.answer(callback_text)
        return
    await target.answer(text, reply_markup=reply_markup)


@admin_router.message(Command("admin"))
async def admin_menu_handler(message: Message):
    if message.from_user.id != config.ADMIN_ID:
        await message.answer(bot_content.message("not_admin"))
        return
    await message.answer(bot_content.message("admin_menu"), reply_markup=get_admin_menu_kb())


@admin_router.message(Command("schedule"))
async def admin_schedule_command(message: Message):
    if message.from_user.id != config.ADMIN_ID:
        await message.answer(bot_content.message("not_admin"))
        return
    await send_admin_schedule(message, now_in_app_tz().date())


@admin_router.message(Command("stats"))
async def admin_stats_command(message: Message):
    if message.from_user.id != config.ADMIN_ID:
        await message.answer(bot_content.message("not_admin"))
        return
    async with async_session() as session:
        text = await load_admin_stats(session)
    await message.answer(text, reply_markup=get_admin_menu_kb())


@admin_router.callback_query(F.data == "admin_stats")
async def handle_admin_stats(callback: CallbackQuery):
    if not is_admin(callback):
        await callback.answer(bot_content.message("not_admin"), show_alert=True)
        return

    async with async_session() as session:
        text = await load_admin_stats(session)
    await callback.message.edit_text(text, reply_markup=get_admin_menu_kb())
    await callback.answer()


@admin_router.callback_query(F.data.startswith("admin_schedule_"))
async def handle_admin_schedule(callback: CallbackQuery):
    if not is_admin(callback):
        await callback.answer(bot_content.message("not_admin"), show_alert=True)
        return

    try:
        target_date = parse_schedule_date(callback.data.removeprefix("admin_schedule_"))
    except ValueError:
        await callback.answer(bot_content.message("admin_invalid_date"), show_alert=True)
        return

    await send_admin_schedule(callback, target_date)


@admin_router.callback_query(F.data.startswith("admin_post_"))
async def handle_admin_post_manage(callback: CallbackQuery):
    if not is_admin(callback):
        await callback.answer(bot_content.message("not_admin"), show_alert=True)
        return

    _, _, post_id_raw, return_date_raw = callback.data.split("_", 3)
    try:
        post_id = int(post_id_raw)
        return_date = date.fromisoformat(return_date_raw)
    except ValueError:
        await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
        return

    async with async_session() as session:
        post = await load_post(session, post_id)

    if not post or post.status != PostStatus.APPROVED:
        await callback.answer(bot_content.message("admin_post_not_scheduled"), show_alert=True)
        return

    await callback.message.edit_text(
        admin_post_manage_text(post),
        reply_markup=get_admin_post_manage_kb(post.id, return_date),
    )
    await callback.answer()


@admin_router.callback_query(F.data.startswith("admin_publish_"))
async def handle_admin_publish_now(callback: CallbackQuery, bot: Bot):
    if not is_admin(callback):
        await callback.answer(bot_content.message("not_admin"), show_alert=True)
        return

    _, _, post_id_raw, return_date_raw = callback.data.split("_", 3)
    try:
        post_id = int(post_id_raw)
        return_date = date.fromisoformat(return_date_raw)
    except ValueError:
        await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
        return

    async with async_session() as session:
        post = await lock_post(session, post_id)
        if not post or post.status != PostStatus.APPROVED:
            await callback.answer(bot_content.message("admin_post_not_scheduled"), show_alert=True)
            return

        try:
            await publish_post(bot, session, post, published_at=now_in_app_tz())
        except Exception:
            await callback.answer(bot_content.message("admin_publish_failed"), show_alert=True)
            return

    await send_admin_schedule(callback, return_date, callback_text=bot_content.message("admin_published_now"))


@admin_router.callback_query(F.data.startswith("admin_reschedule_"))
async def handle_admin_reschedule_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback):
        await callback.answer(bot_content.message("not_admin"), show_alert=True)
        return

    _, _, post_id_raw, return_date_raw = callback.data.split("_", 3)
    try:
        post_id = int(post_id_raw)
        return_date = date.fromisoformat(return_date_raw)
    except ValueError:
        await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
        return

    async with async_session() as session:
        post = await load_post(session, post_id)

    if not post or post.status != PostStatus.APPROVED:
        await callback.answer(bot_content.message("admin_post_not_scheduled"), show_alert=True)
        return

    await state.set_state(AdminState.waiting_for_reschedule_time)
    await state.update_data(post_id=post_id, return_date=return_date.isoformat())
    await callback.message.edit_text(
        bot_content.message(
            "admin_reschedule_prompt",
            post_id=post_id,
            current_schedule=format_schedule(post.schedule_time),
        ),
        reply_markup=get_admin_reschedule_cancel_kb(return_date),
    )
    await callback.answer()


@admin_router.callback_query(F.data.startswith("admin_cancel_reschedule_"))
async def handle_admin_cancel_reschedule(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback):
        await callback.answer(bot_content.message("not_admin"), show_alert=True)
        return

    try:
        return_date = date.fromisoformat(callback.data.removeprefix("admin_cancel_reschedule_"))
    except ValueError:
        return_date = now_in_app_tz().date()
    await state.clear()
    await send_admin_schedule(callback, return_date)


@admin_router.message(AdminState.waiting_for_reschedule_time)
async def handle_admin_reschedule_text(message: Message, state: FSMContext):
    if message.from_user.id != config.ADMIN_ID:
        await message.answer(bot_content.message("not_admin"))
        return

    data = await state.get_data()
    post_id = int(data.get("post_id") or 0)
    return_date = parse_schedule_date(data.get("return_date"))
    new_schedule = parse_admin_datetime(message.text or "")
    if new_schedule is None:
        await message.answer(
            bot_content.message("admin_reschedule_invalid"),
            reply_markup=get_admin_reschedule_cancel_kb(return_date),
        )
        return

    async with async_session() as session:
        post = await load_post(session, post_id)
        if not post or post.status != PostStatus.APPROVED:
            await state.clear()
            await message.answer(bot_content.message("admin_post_not_scheduled"))
            return

        post.schedule_time = new_schedule
        post.is_auto_scheduled = False
        await session.commit()

    await state.clear()
    await message.answer(
        bot_content.message(
            "admin_reschedule_saved",
            post_id=post_id,
            schedule=format_schedule(new_schedule),
        ),
    )
    await send_admin_schedule(message, new_schedule.date())


@admin_router.callback_query(F.data.startswith("admin_album_"))
async def handle_admin_album_navigation(callback: CallbackQuery):
    if not is_admin(callback):
        await callback.answer(bot_content.message("not_admin"), show_alert=True)
        return

    try:
        _, _, direction, post_id_raw = callback.data.split("_", 3)
        post_id = int(post_id_raw)
    except (TypeError, ValueError):
        await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
        return

    async with async_session() as session:
        post = await load_post(session, post_id)
        if not post or not post.submission_group_id:
            await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
            return

        posts = await load_submission_group_posts(session, post)
        current_index = next((index for index, item in enumerate(posts) if item.id == post.id), 0)
        offset = -1 if direction == "prev" else 1
        target_post = posts[(current_index + offset) % len(posts)]
        await edit_admin_album_view_message(callback.message, session, target_post, use_media=True)

    await callback.answer()


@admin_router.callback_query(F.data.startswith("admin_approve_"))
async def handle_admin_approve(callback: CallbackQuery, bot: Bot):
    if not is_admin(callback):
        await callback.answer(bot_content.message("not_admin"), show_alert=True)
        return

    post_id = int(callback.data.split("_")[2])

    async with async_session() as session:
        post = await load_post(session, post_id)

        if not post or post.status != PostStatus.PENDING:
            await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
            return

        if post.schedule_time is None:
            schedule_time = await get_next_auto_slot(session)
            post.schedule_time = schedule_time
        else:
            schedule_time = post.schedule_time

        post.status = PostStatus.APPROVED
        await ensure_animal_type(session, post.animal_type)
        score_award = await award_post_approval_score(session, post)
        await session.commit()

        schedule_text = format_schedule(schedule_time)
        if callback_is_album_control(callback, post):
            await refresh_admin_album_control(callback, session, post)
        else:
            await callback.message.edit_caption(
                caption=bot_content.message(
                    "approved_caption",
                    schedule=schedule_text,
                    points=score_award.points,
                )
            )

        try:
            await bot.send_message(
                chat_id=post.user.telegram_id,
                text=approved_user_notification_text(
                    post,
                    schedule=schedule_text,
                    points=score_award.points,
                ),
            )
        except TelegramAPIError:
            pass
        await callback.answer(approved_callback_text(post, points=score_award.points))


@admin_router.callback_query(F.data.startswith("admin_reject_"))
async def handle_admin_reject_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback):
        await callback.answer(bot_content.message("not_admin"), show_alert=True)
        return

    post_id = int(callback.data.split("_")[2])

    async with async_session() as session:
        post = await load_post(session, post_id)

        if not post or post.status != PostStatus.PENDING:
            await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
            return

        is_album_control = callback_is_album_control(callback, post)

    await state.set_state(AdminState.waiting_for_rejection_reason)
    await state.update_data(
        reject_post_id=post_id,
        reject_message_chat_id=callback.message.chat.id,
        reject_message_id=callback.message.message_id,
        reject_is_album_control=is_album_control,
        reject_is_album_view=bool(callback.message.photo),
    )
    prompt = bot_content.message("admin_rejection_reason_prompt", post_id=post_id)
    reply_markup = get_admin_rejection_reason_kb(post_id, has_duplicate=post.duplicate_of_photo_id is not None)
    if is_album_control and callback.message.text:
        await callback.message.edit_text(prompt, reply_markup=reply_markup)
    else:
        await callback.message.edit_caption(caption=prompt, reply_markup=reply_markup)
    await callback.answer()


@admin_router.callback_query(F.data.startswith("admin_rejectreason_none_"))
async def handle_admin_reject_without_reason(callback: CallbackQuery, state: FSMContext, bot: Bot):
    if not is_admin(callback):
        await callback.answer(bot_content.message("not_admin"), show_alert=True)
        return

    post_id = int(callback.data.rsplit("_", 1)[1])
    async with async_session() as session:
        post = await load_post(session, post_id)
        if not post or post.status != PostStatus.PENDING:
            await state.clear()
            await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
            return

        is_album_control = callback_is_album_control(callback, post)
        is_album_view = bool(callback.message.photo)
        await reject_post(session, post)
        await edit_admin_rejection_result(
            bot,
            session,
            post,
            chat_id=callback.message.chat.id,
            message_id=callback.message.message_id,
            is_album_control=is_album_control,
            is_album_view=is_album_view,
        )
        await notify_rejected_post_user(bot, post)

    await state.clear()
    await callback.answer(bot_content.message("rejected_callback"))


@admin_router.callback_query(F.data.startswith("admin_rejectreason_duplicate_"))
async def handle_admin_reject_as_duplicate(callback: CallbackQuery, state: FSMContext, bot: Bot):
    if not is_admin(callback):
        await callback.answer(bot_content.message("not_admin"), show_alert=True)
        return

    post_id = int(callback.data.rsplit("_", 1)[1])
    async with async_session() as session:
        post = await load_post(session, post_id)
        if not post or post.status != PostStatus.PENDING:
            await state.clear()
            await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
            return

        reason = duplicate_rejection_reason(post)
        if reason is None:
            await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
            return

        is_album_control = callback_is_album_control(callback, post)
        is_album_view = bool(callback.message.photo)
        await reject_post(session, post, reason=reason)
        await edit_admin_rejection_result(
            bot,
            session,
            post,
            chat_id=callback.message.chat.id,
            message_id=callback.message.message_id,
            is_album_control=is_album_control,
            is_album_view=is_album_view,
            reason=reason,
        )
        await notify_rejected_post_user(bot, post, reason=reason)

    await state.clear()
    await callback.answer(bot_content.message("rejected_callback"))


@admin_router.message(AdminState.waiting_for_rejection_reason)
async def handle_admin_rejection_reason_text(message: Message, state: FSMContext, bot: Bot):
    if message.from_user.id != config.ADMIN_ID:
        await message.answer(bot_content.message("not_admin"))
        return

    raw_reason = normalize_rejection_reason(message.text)
    if raw_reason is None:
        await message.answer(bot_content.message("admin_rejection_reason_empty"))
        return

    data = await state.get_data()
    post_id = int(data.get("reject_post_id") or 0)
    chat_id = int(data.get("reject_message_chat_id") or message.chat.id)
    message_id = int(data.get("reject_message_id") or 0)
    is_album_control = bool(data.get("reject_is_album_control"))
    is_album_view = bool(data.get("reject_is_album_view"))

    async with async_session() as session:
        post = await load_post(session, post_id)
        if not post or post.status != PostStatus.PENDING:
            await state.clear()
            await message.answer(bot_content.message("post_processed_or_missing"))
            return

        reason = normalize_duplicate_rejection_reason(raw_reason, post)
        await reject_post(session, post, reason=reason)
        if message_id:
            await edit_admin_rejection_result(
                bot,
                session,
                post,
                chat_id=chat_id,
                message_id=message_id,
                is_album_control=is_album_control,
                is_album_view=is_album_view,
                reason=reason,
            )
        await notify_rejected_post_user(bot, post, reason=reason)

    await state.clear()
    await message.answer(bot_content.message("rejected_callback"))


@admin_router.callback_query(F.data.startswith("admin_change_"))
async def handle_admin_change(callback: CallbackQuery):
    if not is_admin(callback):
        await callback.answer(bot_content.message("not_admin"), show_alert=True)
        return

    post_id = int(callback.data.split("_")[2])
    async with async_session() as session:
        post = await load_post(session, post_id)
        if not post or post.status != PostStatus.PENDING:
            await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
            return

    await callback.message.edit_reply_markup(reply_markup=await get_admin_animal_change_kb(post_id))
    await callback.answer()


@admin_router.callback_query(F.data.startswith("admin_back_"))
async def handle_admin_back(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback):
        await callback.answer(bot_content.message("not_admin"), show_alert=True)
        return

    await state.clear()
    post_id = int(callback.data.split("_")[2])
    async with async_session() as session:
        post = await load_post(session, post_id)
        if not post:
            await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
            return

        if callback_is_album_control(callback, post):
            await refresh_admin_album_control(callback, session, post)
        else:
            await callback.message.edit_reply_markup(reply_markup=get_admin_approval_kb(post_id))

    await callback.answer()


@admin_router.callback_query(F.data.startswith("admin_setanimal_"))
async def handle_admin_set_animal(callback: CallbackQuery):
    if not is_admin(callback):
        await callback.answer(bot_content.message("not_admin"), show_alert=True)
        return

    _, _, post_id_raw, animal_type_id_raw = callback.data.split("_", 3)
    post_id = int(post_id_raw)
    try:
        animal_type_id = int(animal_type_id_raw)
    except ValueError:
        await callback.answer(bot_content.message("animal_type_not_found"), show_alert=True)
        return

    async with async_session() as session:
        animal_type = await get_animal_type_name(session, animal_type_id)
        if not animal_type:
            await callback.answer(bot_content.message("animal_type_not_found"), show_alert=True)
            return

        post = await load_post(session, post_id)
        if not post or post.status != PostStatus.PENDING:
            await callback.answer(bot_content.message("post_processed_or_missing"), show_alert=True)
            return

        post.animal_type = animal_type
        await session.commit()

        if callback_is_album_control(callback, post):
            await refresh_admin_album_control(callback, session, post)
        else:
            await callback.message.edit_caption(
                caption=admin_post_caption(post),
                reply_markup=get_admin_approval_kb(post_id),
            )

    await callback.answer(bot_content.message("animal_changed"))
