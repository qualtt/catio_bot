from datetime import date, time
from types import SimpleNamespace

import pytest

from bot.handlers import suggest
from db.crud import combine_slot
from db.models.user import User


class FakeBot:
    def __init__(self):
        self.sent_photos = []
        self.sent_messages = []

    async def send_photo(self, **kwargs):
        self.sent_photos.append(kwargs)

    async def send_message(self, **kwargs):
        self.sent_messages.append(kwargs)


def test_album_schedule_context_normalizes_state():
    data = {
        "album_items": [{"animal_type": "кот"}, {"animal_type": "птица"}],
        "album_schedule_times": ["2026-07-06T10:00:00+03:00"],
        "album_schedule_auto_flags": [True],
        "album_schedule_index": 99,
    }

    items, schedule_times, schedule_auto_flags, schedule_index = suggest._album_schedule_context(data)

    assert len(items) == 2
    assert schedule_times[0].isoformat() == "2026-07-06T10:00:00+03:00"
    assert schedule_times[1] is None
    assert schedule_auto_flags == [True, False]
    assert schedule_index == 1


def test_photo_type_prompts_include_duplicate_warning_before_type_selection():
    single_text = suggest._single_photo_prompt_text(
        {
            "duplicate_of_photo_id": 42,
            "duplicate_distance": 0,
        }
    )
    album_text = suggest._album_prompt_text(
        {
            "album_index": 0,
            "album_items": [
                {
                    "duplicate_of_photo_id": 99,
                    "duplicate_distance": 3,
                }
            ],
        }
    )

    assert single_text.startswith("Кто на фото?")
    assert "точный дубль" in single_text.lower()
    assert album_text.startswith("Фото 1 из 1. Кто на фото?")
    assert "Похоже на уже известное фото #99" in album_text


@pytest.mark.asyncio
async def test_duplicate_original_is_sent_to_admin_for_comparison():
    bot = FakeBot()
    post = SimpleNamespace(
        id=7,
        duplicate_of_photo_id=42,
        duplicate_of_photo=SimpleNamespace(telegram_file_id="original-file-id"),
    )

    await suggest._send_duplicate_original_to_admin(bot, post=post)

    assert bot.sent_messages == []
    assert bot.sent_photos == [
        {
            "chat_id": 1,
            "photo": "original-file-id",
            "caption": "Оригинал для сравнения с заявкой #7: фото #42.",
        }
    ]


@pytest.mark.asyncio
async def test_duplicate_original_missing_file_id_sends_admin_notice():
    bot = FakeBot()
    post = SimpleNamespace(
        id=7,
        duplicate_of_photo_id=42,
        duplicate_of_photo=SimpleNamespace(telegram_file_id=None),
    )

    await suggest._send_duplicate_original_to_admin(bot, post=post)

    assert bot.sent_photos == []
    assert bot.sent_messages == [
        {
            "chat_id": 1,
            "text": "Оригинал для сравнения с заявкой #7 не удалось отправить: у фото #42 нет Telegram file_id.",
        }
    ]


def test_album_selected_slots_can_exclude_current_item():
    first = combine_slot(date(2026, 7, 6), time(10, 0))
    second = combine_slot(date(2026, 7, 7), time(12, 0))
    data = {
        "album_items": [{}, {}],
        "album_schedule_times": [first.isoformat(), second.isoformat()],
    }

    assert suggest._album_selected_slots(data, exclude_index=0) == {second}


def test_album_calendar_availability_excludes_selected_slots():
    selected = {
        combine_slot(date(2026, 7, 6), time(10, 0)),
        combine_slot(date(2026, 7, 6), time(12, 0)),
    }
    availability = {
        date(2026, 7, 6): 2,
        date(2026, 7, 7): 1,
    }

    adjusted = suggest._subtract_selected_album_slots(availability, selected)
    free_times = suggest._filter_selected_album_times(
        [time(10, 0), time(12, 0), time(14, 0)],
        date(2026, 7, 6),
        selected,
    )

    assert adjusted[date(2026, 7, 6)] == 0
    assert adjusted[date(2026, 7, 7)] == 1
    assert free_times == [time(14, 0)]


@pytest.mark.asyncio
async def test_create_album_posts_preserves_per_photo_schedule_flags(db_session):
    user = User(telegram_id=1001, username="user", full_name="User")
    db_session.add(user)
    await db_session.flush()

    schedule_times = [
        combine_slot(date(2026, 7, 6), time(10, 0)),
        combine_slot(date(2026, 7, 7), time(12, 0)),
    ]
    data = {
        "user_id": user.id,
        "submission_group_id": "album-1",
        "album_items": [
            {"file_id": "file-1", "animal_type": "кот"},
            {"file_id": "file-2", "animal_type": "птица"},
        ],
    }

    posts = await suggest._create_album_posts(
        db_session,
        data=data,
        schedule_times=schedule_times,
        schedule_auto_flags=[False, True],
    )

    assert [post.file_id for post in posts] == ["file-1", "file-2"]
    assert [post.is_auto_scheduled for post in posts] == [False, True]
    assert [post.submission_group_index for post in posts] == [1, 2]
    assert {post.submission_group_size for post in posts} == {2}


@pytest.mark.asyncio
async def test_create_album_posts_rejects_invalid_schedule_state(db_session):
    with pytest.raises(RuntimeError):
        await suggest._create_album_posts(
            db_session,
            data={"user_id": 1, "album_items": [{"file_id": "file-1"}]},
            schedule_times=[],
            schedule_auto_flags=[],
        )
