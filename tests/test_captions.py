from datetime import datetime
from types import SimpleNamespace

from bot.services.captions import album_submission_photo_caption, submission_caption
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

    assert "2. Заявка #7" in caption
    assert "Вид: птица" in caption
    assert "2026-07-06 12:00" in caption
    assert "похоже на #99" in caption
