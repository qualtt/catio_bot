from datetime import UTC, datetime
from types import SimpleNamespace

from bot.services.captions import admin_album_view_caption, album_submission_photo_caption, submission_caption
from db.models.post import PostStatus


def test_submission_caption_uses_admin_wording_and_duplicate_note():
    caption = submission_caption(
        animal_type="кот",
        schedule="2026-07-06 10:00",
        author="@user",
        duplicate_of_photo_id=42,
        duplicate_distance=0,
    )

    assert "Вид: кот" in caption
    assert "Публикация: 2026-07-06 10:00" in caption
    assert "точный дубль" in caption.lower()


def test_album_submission_photo_caption_contains_number_status_and_schedule():
    post = SimpleNamespace(
        id=7,
        animal_type="птица",
        schedule_time=datetime(2026, 7, 6, 12, 0),
        duplicate_of_photo_id=99,
        duplicate_distance=3,
        status=PostStatus.PENDING,
    )

    caption = album_submission_photo_caption(post, number=2)

    assert "2. Заявка /post_7" in caption
    assert "Вид: птица" in caption
    assert "2026-07-06 12:00" in caption
    assert "похоже на /photo_99" in caption


def test_album_submission_photo_caption_renders_schedule_in_app_timezone():
    post = SimpleNamespace(
        id=7,
        animal_type="птица",
        schedule_time=datetime(2026, 7, 6, 8, 0, tzinfo=UTC),
        duplicate_of_photo_id=None,
        duplicate_distance=None,
        status=PostStatus.PENDING,
    )

    caption = album_submission_photo_caption(post, number=2)

    assert "2026-07-06 11:00" in caption
    assert "2026-07-06 08:00" not in caption


def test_admin_album_view_caption_contains_current_position_and_status():
    posts = [
        SimpleNamespace(
            id=7,
            animal_type="кот",
            schedule_time=datetime(2026, 7, 6, 12, 0),
            duplicate_of_photo_id=None,
            duplicate_distance=None,
            submission_group_index=1,
            status=PostStatus.PENDING,
        ),
        SimpleNamespace(
            id=8,
            animal_type="птица",
            schedule_time=datetime(2026, 7, 7, 12, 0),
            duplicate_of_photo_id=None,
            duplicate_distance=None,
            submission_group_index=2,
            status=PostStatus.APPROVED,
        ),
    ]

    caption = admin_album_view_caption(posts, posts[1], author="@user")

    assert "От: @user" in caption
    assert "Фото: 2/2" in caption
    assert "Статус: запланировано" in caption
