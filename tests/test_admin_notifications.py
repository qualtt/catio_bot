from types import SimpleNamespace

from bot.handlers.admin import (
    approved_callback_text,
    approved_user_notification_text,
    duplicate_rejection_reason,
    normalize_rejection_reason,
    normalize_duplicate_rejection_reason,
    rejected_admin_caption,
    rejected_user_notification_text,
)


def make_post(*, submission_group_id=None):
    return SimpleNamespace(
        animal_type="кот",
        submission_group_id=submission_group_id,
        duplicate_of_photo_id=None,
        duplicate_distance=None,
    )


def test_album_approval_text_mentions_points_for_single_photo():
    post = make_post(submission_group_id="album-1")

    user_text = approved_user_notification_text(post, schedule="2026-07-06 10:00", points=100)
    callback_text = approved_callback_text(post, points=100)

    assert "за это фото" in user_text
    assert "+100 за фото" in callback_text


def test_single_approval_text_keeps_regular_points_wording():
    post = make_post()

    user_text = approved_user_notification_text(post, schedule="2026-07-06 10:00", points=120)
    callback_text = approved_callback_text(post, points=120)

    assert "Баллы: +120" in user_text
    assert callback_text == "Одобрено. +120"


def test_rejection_reason_is_normalized_and_rendered_for_user_and_admin():
    post = make_post()
    reason = normalize_rejection_reason("  не подходит   качество  ")

    assert reason == "не подходит качество"
    assert "Причина: не подходит качество" in rejected_admin_caption(reason)
    assert "Причина: не подходит качество" in rejected_user_notification_text(post, reason=reason)


def test_duplicate_rejection_reason_can_be_selected_with_short_text():
    post = make_post()
    post.duplicate_of_photo_id = 42
    post.duplicate_distance = 0

    reason = normalize_duplicate_rejection_reason(" есть копия ", post)

    assert reason == duplicate_rejection_reason(post)
    assert reason == "копия уже известного фото #42"


def test_album_rejection_text_uses_album_wording():
    post = make_post(submission_group_id="album-1")

    assert rejected_user_notification_text(post) == "Фото из альбома было отклонено администратором."
    assert "Причина: повтор" in rejected_user_notification_text(post, reason="повтор")
