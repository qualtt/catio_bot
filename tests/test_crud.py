from datetime import date, time, timedelta

import pytest

from db import crud
from db.models.animal_type import AnimalType
from db.models.channel_history import ChannelHistory
from db.models.photo import Photo
from db.models.post import Post, PostStatus
from db.models.user import User


@pytest.mark.asyncio
async def test_create_channel_history_item_normalizes_numeric_media_group_id(db_session):
    item = await crud.create_channel_history_item(
        db_session,
        chat_id=-1001452038450,
        message_id=902,
        file_id="file-id",
        media_group_id=13706980407056186,
    )

    assert item.media_group_id == "13706980407056186"


@pytest.mark.asyncio
async def test_free_slots_and_day_availability_ignore_rejected_posts(db_session, monkeypatch):
    monkeypatch.setattr(crud.config, "DAILY_SLOT_TIMES", "10:00,12:00,14:00")
    target_date = date(2026, 7, 6)
    user = User(telegram_id=1001, username="user", full_name="User")
    db_session.add(user)
    await db_session.flush()
    db_session.add_all(
        [
            Post(
                user_id=user.id,
                file_id="pending",
                animal_type="кот",
                status=PostStatus.PENDING,
                schedule_time=crud.combine_slot(target_date, time(10, 0)),
            ),
            Post(
                user_id=user.id,
                file_id="approved",
                animal_type="кот",
                status=PostStatus.APPROVED,
                schedule_time=crud.combine_slot(target_date, time(12, 0)),
            ),
            Post(
                user_id=user.id,
                file_id="rejected",
                animal_type="кот",
                status=PostStatus.REJECTED,
                schedule_time=crud.combine_slot(target_date, time(14, 0)),
            ),
        ]
    )
    await db_session.commit()

    free_times = await crud.get_free_slot_times(db_session, target_date)
    availability = await crud.get_day_availability(db_session, start_date=target_date, days=1)

    assert free_times == [time(14, 0)]
    assert availability[target_date] == 1


@pytest.mark.asyncio
async def test_next_auto_slot_uses_empty_days_not_partially_free_days(db_session, monkeypatch):
    monkeypatch.setattr(crud.config, "DAILY_SLOT_TIMES", "10:00,12:00")
    monkeypatch.setattr(crud.config, "AUTO_POST_DAYS_AHEAD", 2)
    tomorrow = crud.now_in_app_tz().date() + timedelta(days=1)
    user = User(telegram_id=1001, username="user", full_name="User")
    db_session.add(user)
    await db_session.flush()
    db_session.add(
        Post(
            user_id=user.id,
            file_id="approved",
            animal_type="кот",
            status=PostStatus.APPROVED,
            schedule_time=crud.combine_slot(tomorrow, time(10, 0)),
        )
    )
    await db_session.commit()

    auto_slot = await crud.get_next_auto_slot(db_session)

    assert auto_slot == crud.combine_slot(tomorrow + timedelta(days=1), time(10, 0))


@pytest.mark.asyncio
async def test_animal_type_options_are_ordered_by_photo_count(db_session):
    db_session.add_all(
        [
            AnimalType(name="кот", is_primary=True, sort_order=10),
            AnimalType(name="птица", is_primary=True, sort_order=20),
            AnimalType(name="крыса", is_primary=True, sort_order=30),
        ]
    )
    user = User(telegram_id=1001, username="user", full_name="User")
    db_session.add(user)
    await db_session.flush()
    db_session.add_all(
        [
            Post(user_id=user.id, file_id="1", animal_type="птица", status=PostStatus.APPROVED),
            Post(user_id=user.id, file_id="2", animal_type="птица", status=PostStatus.PUBLISHED),
            Post(user_id=user.id, file_id="3", animal_type="кот", status=PostStatus.APPROVED),
            Post(user_id=user.id, file_id="4", animal_type="крыса", status=PostStatus.PENDING),
        ]
    )
    await db_session.commit()

    options = await crud.get_animal_type_options(db_session, is_primary=True)

    assert [(option.name, option.photo_count) for option in options] == [
        ("птица", 2),
        ("кот", 1),
        ("крыса", 0),
    ]


@pytest.mark.asyncio
async def test_canonical_and_ensure_animal_type_reuse_existing_rows(db_session):
    existing = AnimalType(name="Кот", is_primary=True, sort_order=10)
    db_session.add(existing)
    await db_session.commit()

    canonical = await crud.canonical_animal_type(db_session, "  кот  ")
    ensured = await crud.ensure_animal_type(db_session, "кот")

    assert canonical == "Кот"
    assert ensured.id == existing.id


@pytest.mark.asyncio
async def test_user_can_view_public_or_own_photo_only(db_session):
    owner = User(telegram_id=1001, username="owner", full_name="Owner")
    other = User(telegram_id=1002, username="other", full_name="Other")
    public_photo = Photo(storage_bucket="bucket", storage_key="public.jpg")
    private_photo = Photo(storage_bucket="bucket", storage_key="private.jpg")
    db_session.add_all([owner, other, public_photo, private_photo])
    await db_session.flush()
    db_session.add_all(
        [
            ChannelHistory(message_id=10, file_id="public-file", photo_id=public_photo.id),
            Post(user_id=owner.id, file_id="private-file", photo_id=private_photo.id),
        ]
    )
    await db_session.commit()

    assert await crud.user_can_view_photo(db_session, photo_id=public_photo.id, telegram_id=other.telegram_id)
    assert await crud.user_can_view_photo(db_session, photo_id=private_photo.id, telegram_id=owner.telegram_id)
    assert not await crud.user_can_view_photo(db_session, photo_id=private_photo.id, telegram_id=other.telegram_id)
    assert await crud.user_can_view_photo(
        db_session,
        photo_id=private_photo.id,
        telegram_id=other.telegram_id,
        is_admin=True,
    )


@pytest.mark.asyncio
async def test_random_public_photo_ignores_private_submissions(db_session):
    user = User(telegram_id=1001, username="owner", full_name="Owner")
    public_photo = Photo(storage_bucket="bucket", storage_key="public.jpg")
    private_photo = Photo(storage_bucket="bucket", storage_key="private.jpg")
    db_session.add_all([user, public_photo, private_photo])
    await db_session.flush()
    db_session.add_all(
        [
            ChannelHistory(message_id=10, file_id="public-file", photo_id=public_photo.id),
            Post(user_id=user.id, file_id="private-file", photo_id=private_photo.id),
        ]
    )
    await db_session.commit()

    photo = await crud.get_random_public_photo(db_session)

    assert photo is not None
    assert photo.id == public_photo.id
