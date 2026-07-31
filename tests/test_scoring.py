import pytest

from bot.services import scoring
from db.models.post import Post
from db.models.user import User


@pytest.mark.asyncio
async def test_post_approval_score_applies_auto_bonus_and_duplicate_factor(db_session, monkeypatch):
    async def fake_bonus(_session):
        return 20

    monkeypatch.setattr(scoring.config, "SCORE_APPROVED_POST_BASE", 100)
    monkeypatch.setattr(scoring.config, "SCORE_DUPLICATE_EXACT_FACTOR_PERCENT", 0)
    monkeypatch.setattr(scoring.config, "SCORE_DUPLICATE_SIMILAR_FACTOR_PERCENT", 50)
    monkeypatch.setattr(scoring, "calculate_auto_bonus_percent", fake_bonus)

    clean_auto = Post(user_id=1, file_id="1", animal_type="кот", is_auto_scheduled=True)
    exact_duplicate = Post(
        user_id=1,
        file_id="2",
        animal_type="кот",
        is_auto_scheduled=False,
        duplicate_of_photo_id=10,
        duplicate_distance=0,
    )
    similar_duplicate = Post(
        user_id=1,
        file_id="3",
        animal_type="кот",
        is_auto_scheduled=False,
        duplicate_of_photo_id=10,
        duplicate_distance=3,
    )

    assert (await scoring.calculate_post_approval_score(db_session, clean_auto)).points == 120
    assert (await scoring.calculate_post_approval_score(db_session, exact_duplicate)).points == 0
    assert (await scoring.calculate_post_approval_score(db_session, similar_duplicate)).points == 50


@pytest.mark.asyncio
async def test_record_score_event_is_idempotent(db_session):
    user = User(telegram_id=1001, username="user", full_name="User")
    db_session.add(user)
    await db_session.commit()

    first = await scoring.record_score_event(
        db_session,
        user_id=user.id,
        event_type="post_approved",
        points=100,
        entity_type="post",
        entity_id=1,
        details={"reason": "approved"},
    )
    second = await scoring.record_score_event(
        db_session,
        user_id=user.id,
        event_type="post_approved",
        points=100,
        entity_type="post",
        entity_id=1,
        details={"reason": "approved"},
    )

    assert first.created is True
    assert second.created is False
    assert user.score == 100
