from datetime import date, time, timedelta

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.config import config
from bot.content import bot_content
from bot.services.identification import ITEM_REJECTED
from db.crud import AnimalTypeOption, ensure_app_timezone, get_animal_type_options
from db.database import async_session
from db.models.post import PostStatus


def _two_column_rows(item_count: int, footer_count: int = 0) -> list[int]:
    rows = [2] * (item_count // 2)
    if item_count % 2:
        rows.append(1)
    rows.extend([1] * footer_count)
    return rows or [1]


def get_main_menu_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=bot_content.button("identify_old_photos"), callback_data="identify_next")
    builder.button(text=bot_content.button("photo_tournament"), callback_data="tourn_current")
    builder.adjust(1)
    return builder.as_markup()


def _add_album_nav_buttons(builder: InlineKeyboardBuilder, *, with_album_nav: bool) -> None:
    if not with_album_nav:
        return
    builder.button(text=bot_content.button("album_prev"), callback_data="album_prev")
    builder.button(text=bot_content.button("album_next"), callback_data="album_next")


async def get_animal_type_kb(*, with_album_nav: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    async with async_session() as session:
        animal_types = await get_animal_type_options(session, is_primary=True)

    for animal_type in animal_types:
        builder.button(text=animal_type.name, callback_data=f"animal_id_{animal_type.id}")
    builder.button(text=bot_content.other_animal_label(), callback_data="animal_other")
    _add_album_nav_buttons(builder, with_album_nav=with_album_nav)
    builder.adjust(*_two_column_rows(len(animal_types), footer_count=1), *([2] if with_album_nav else []))
    return builder.as_markup()


async def get_other_animal_type_kb(*, with_album_nav: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    async with async_session() as session:
        animal_types = await get_animal_type_options(session, is_primary=False)

    for animal_type in animal_types:
        builder.button(text=animal_type.name, callback_data=f"animal_extra_id_{animal_type.id}")
    builder.button(text=bot_content.button("custom_animal_type"), callback_data="animal_custom")
    builder.button(text=bot_content.button("back"), callback_data="animal_back")
    _add_album_nav_buttons(builder, with_album_nav=with_album_nav)
    builder.adjust(*_two_column_rows(len(animal_types), footer_count=2), *([2] if with_album_nav else []))
    return builder.as_markup()

def get_schedule_choice_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=bot_content.button(
            "schedule_auto",
            points=config.SCORE_APPROVED_POST_BASE,
            bonus_min=config.SCORE_AUTO_BONUS_MIN_PERCENT,
            bonus_max=config.SCORE_AUTO_BONUS_MAX_PERCENT,
        ),
        callback_data="schedule_auto",
    )
    builder.button(
        text=bot_content.button("schedule_manual", points=config.SCORE_APPROVED_POST_BASE),
        callback_data="schedule_manual",
    )
    builder.adjust(1)
    return builder.as_markup()

def get_admin_approval_kb(post_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=bot_content.button("approve"), callback_data=f"admin_approve_{post_id}")
    builder.button(text=bot_content.button("reject"), callback_data=f"admin_reject_{post_id}")
    builder.button(text=bot_content.button("change_animal"), callback_data=f"admin_change_{post_id}")
    builder.adjust(2, 1)
    return builder.as_markup()


def get_admin_menu_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=bot_content.button("admin_schedule"), callback_data="admin_schedule_today")
    builder.button(text=bot_content.button("admin_stats"), callback_data="admin_stats")
    builder.button(text=bot_content.button("admin_broadcast"), callback_data="admin_broadcast")
    builder.adjust(1)
    return builder.as_markup()


def get_admin_broadcast_confirm_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=bot_content.button("admin_broadcast_send"), callback_data="admin_broadcast_send")
    builder.button(text=bot_content.button("admin_broadcast_cancel"), callback_data="admin_broadcast_cancel")
    builder.adjust(2)
    return builder.as_markup()


def get_admin_schedule_kb(target_date: date, posts) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for post in posts:
        schedule = ensure_app_timezone(post.schedule_time).strftime("%H:%M") if post.schedule_time else "--:--"
        animal_type = post.animal_type or "?"
        builder.button(
            text=bot_content.button(
                "admin_schedule_post",
                post_id=post.id,
                time=schedule,
                animal_type=animal_type,
            ),
            callback_data=f"admin_post_{post.id}_{target_date.isoformat()}",
        )

    previous_day = target_date - timedelta(days=1)
    next_day = target_date + timedelta(days=1)
    builder.button(
        text=bot_content.button("admin_schedule_prev_day"),
        callback_data=f"admin_schedule_{previous_day.isoformat()}",
    )
    builder.button(
        text=bot_content.button("admin_schedule_today"),
        callback_data="admin_schedule_today",
    )
    builder.button(
        text=bot_content.button("admin_schedule_next_day"),
        callback_data=f"admin_schedule_{next_day.isoformat()}",
    )
    builder.button(text=bot_content.button("admin_stats"), callback_data="admin_stats")
    builder.adjust(*([1] * len(posts)), 3, 1)
    return builder.as_markup()


def get_admin_post_manage_kb(post_id: int, return_date: date) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=bot_content.button("admin_publish_now"),
        callback_data=f"admin_publish_{post_id}_{return_date.isoformat()}",
    )
    builder.button(
        text=bot_content.button("admin_change_time"),
        callback_data=f"admin_reschedule_{post_id}_{return_date.isoformat()}",
    )
    builder.button(
        text=bot_content.button("admin_back_to_schedule"),
        callback_data=f"admin_schedule_{return_date.isoformat()}",
    )
    builder.adjust(1)
    return builder.as_markup()


def get_admin_reschedule_cancel_kb(return_date: date) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=bot_content.button("admin_back_to_schedule"),
        callback_data=f"admin_cancel_reschedule_{return_date.isoformat()}",
    )
    builder.adjust(1)
    return builder.as_markup()


def get_admin_rejection_reason_kb(post_id: int, *, has_duplicate: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if has_duplicate:
        builder.button(
            text=bot_content.button("reject_as_duplicate"),
            callback_data=f"admin_rejectreason_duplicate_{post_id}",
        )
    builder.button(
        text=bot_content.button("reject_without_reason"),
        callback_data=f"admin_rejectreason_none_{post_id}",
    )
    builder.button(text=bot_content.button("back"), callback_data=f"admin_back_{post_id}")
    builder.adjust(1)
    return builder.as_markup()


def get_admin_album_view_kb(posts, current_post) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    ordered_posts = sorted(posts, key=lambda item: item.submission_group_index or item.id)
    if len(ordered_posts) > 1:
        builder.button(text=bot_content.button("album_prev"), callback_data=f"admin_album_prev_{current_post.id}")
        builder.button(text=bot_content.button("album_next"), callback_data=f"admin_album_next_{current_post.id}")

    if current_post.status == PostStatus.PENDING:
        number = current_post.submission_group_index or 1
        builder.button(
            text=bot_content.button("album_approve", number=number),
            callback_data=f"admin_approve_{current_post.id}",
        )
        builder.button(
            text=bot_content.button("album_reject", number=number),
            callback_data=f"admin_reject_{current_post.id}",
        )
        builder.button(
            text=bot_content.button("album_change", number=number),
            callback_data=f"admin_change_{current_post.id}",
        )

    row_sizes = [2] if len(ordered_posts) > 1 else []
    if current_post.status == PostStatus.PENDING:
        row_sizes.append(3)
    if row_sizes:
        builder.adjust(*row_sizes)
    return builder.as_markup()


def get_admin_album_kb(posts) -> InlineKeyboardMarkup | None:
    pending_posts = [
        post
        for post in sorted(posts, key=lambda item: item.submission_group_index or item.id)
        if post.status == PostStatus.PENDING
    ]
    if not pending_posts:
        return None

    builder = InlineKeyboardBuilder()
    for index, post in enumerate(pending_posts, start=1):
        number = post.submission_group_index or index
        builder.button(
            text=bot_content.button("album_approve", number=number),
            callback_data=f"admin_approve_{post.id}",
        )
        builder.button(
            text=bot_content.button("album_reject", number=number),
            callback_data=f"admin_reject_{post.id}",
        )
        builder.button(
            text=bot_content.button("album_change", number=number),
            callback_data=f"admin_change_{post.id}",
        )
    builder.adjust(*([3] * len(pending_posts)))
    return builder.as_markup()


async def get_admin_animal_change_kb(post_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    async with async_session() as session:
        primary_types = await get_animal_type_options(session, is_primary=True)
        other_types = await get_animal_type_options(session, is_primary=False)
    animal_types: list[AnimalTypeOption] = [*primary_types, *other_types]

    for animal_type in animal_types:
        builder.button(text=animal_type.name, callback_data=f"admin_setanimal_{post_id}_{animal_type.id}")
    builder.button(text=bot_content.button("custom_animal_type"), callback_data=f"admin_customanimal_{post_id}")
    builder.button(text=bot_content.button("back"), callback_data=f"admin_back_{post_id}")
    builder.adjust(*_two_column_rows(len(animal_types), footer_count=2))
    return builder.as_markup()


def get_admin_custom_animal_kb(post_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=bot_content.button("back"), callback_data=f"admin_back_{post_id}")
    builder.adjust(1)
    return builder.as_markup()


def get_time_slots_kb(
    target_date: date,
    free_times: list[time],
    footer_buttons: list[tuple[str, str]] | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for slot_time in free_times:
        builder.button(
            text=slot_time.strftime("%H:%M"),
            callback_data=f"time_{target_date.isoformat()}_{slot_time.strftime('%H:%M')}",
        )
    footer_buttons = footer_buttons or []
    for text, callback_data in footer_buttons:
        builder.button(text=text, callback_data=callback_data)
    builder.button(text=bot_content.button("back_to_calendar"), callback_data="schedule_manual")
    builder.adjust(*([3] * ((len(free_times) + 2) // 3)), *([1] * len(footer_buttons)), 1)
    return builder.as_markup()


async def get_identification_animal_type_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    async with async_session() as session:
        animal_types = await get_animal_type_options(session, is_primary=True)

    for animal_type in animal_types:
        builder.button(text=animal_type.name, callback_data=f"identify_animal_id_{animal_type.id}")
    builder.button(text=bot_content.other_animal_label(), callback_data="identify_other")
    builder.adjust(*_two_column_rows(len(animal_types), footer_count=1))
    return builder.as_markup()


async def get_identification_other_animal_type_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    async with async_session() as session:
        animal_types = await get_animal_type_options(session, is_primary=False)

    for animal_type in animal_types:
        builder.button(text=animal_type.name, callback_data=f"identify_animal_extra_id_{animal_type.id}")
    builder.button(text=bot_content.button("custom_animal_type"), callback_data="identify_custom")
    builder.button(text=bot_content.button("back"), callback_data="identify_back")
    builder.adjust(*_two_column_rows(len(animal_types), footer_count=2))
    return builder.as_markup()


def get_identification_continue_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=bot_content.button("identify_next"), callback_data="identify_next")
    builder.adjust(1)
    return builder.as_markup()


def get_identification_batch_view_kb(batch, current_item) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    items = sorted(batch.items, key=lambda item: item.item_number)
    if len(items) > 1:
        builder.button(
            text=bot_content.button("album_prev"),
            callback_data=f"ident_batch_prev_{batch.id}_{current_item.item_number}",
        )
        builder.button(
            text=bot_content.button("album_next"),
            callback_data=f"ident_batch_next_{batch.id}_{current_item.item_number}",
        )

    toggle_button = (
        "identification_batch_include"
        if current_item.status == ITEM_REJECTED
        else "identification_batch_exclude"
    )
    builder.button(
        text=bot_content.button(toggle_button),
        callback_data=f"ident_item_{batch.id}_{current_item.item_number}",
    )
    builder.button(text=bot_content.button("identification_batch_done"), callback_data=f"ident_batch_done_{batch.id}")
    builder.button(
        text=bot_content.button("identification_batch_reject"),
        callback_data=f"ident_batch_reject_{batch.id}",
    )

    row_sizes = [2] if len(items) > 1 else []
    row_sizes.extend([1, 2])
    builder.adjust(*row_sizes)
    return builder.as_markup()


def get_identification_batch_kb(batch) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for item in batch.items:
        prefix = "NO" if item.status == ITEM_REJECTED else "OK"
        builder.button(
            text=f"{prefix} {item.item_number}",
            callback_data=f"ident_item_{batch.id}_{item.item_number}",
        )
    builder.button(text=bot_content.button("identification_batch_done"), callback_data=f"ident_batch_done_{batch.id}")
    builder.button(
        text=bot_content.button("identification_batch_reject"),
        callback_data=f"ident_batch_reject_{batch.id}",
    )
    builder.adjust(*_two_column_rows(len(batch.items), footer_count=2))
    return builder.as_markup()


def get_tournament_start_kb(tournament_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=bot_content.button("tournament_vote"), callback_data=f"tourn_open_{tournament_id}")
    builder.adjust(1)
    return builder.as_markup()


def get_tournament_match_kb(match, *, left_entry_id: int, right_entry_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=bot_content.button("tournament_pick_left"),
        callback_data=f"tourn_vote_{match.id}_{left_entry_id}",
    )
    builder.button(
        text=bot_content.button("tournament_pick_right"),
        callback_data=f"tourn_vote_{match.id}_{right_entry_id}",
    )
    builder.button(text=bot_content.button("tournament_refresh"), callback_data=f"tourn_open_{match.tournament_id}")
    builder.adjust(2, 1)
    return builder.as_markup()
