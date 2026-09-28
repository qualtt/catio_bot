from datetime import timedelta

import pytest

from bot.services import cleanup
from db.crud import get_or_create_user, now_in_app_tz
from db.crud.photos import get_abandoned_photos
from db.models.photo import Photo
from db.models.post import Post, PostStatus


class SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, traceback):
        return False


def _old_photo(photo_id: int) -> Photo:
    return Photo(
        id=photo_id,
        storage_bucket="test-bucket",
        storage_key=f"catio-bot/photos/submissions/{photo_id}.jpg",
        sha256=f"sha-{photo_id}",
        created_at=now_in_app_tz() - timedelta(days=3),
    )


@pytest.fixture
def s3_deletes(db_session, monkeypatch):
    calls = []

    async def fake_delete_photos_batch(*, storage_bucket, storage_keys):
        calls.append((storage_bucket, list(storage_keys)))

    monkeypatch.setattr(cleanup, "async_session", lambda: SessionContext(db_session))
    monkeypatch.setattr(cleanup, "delete_photos_batch", fake_delete_photos_batch)
    return calls


@pytest.mark.asyncio
async def test_cleanup_deletes_abandoned_photo_from_db_and_s3(db_session, s3_deletes):
    db_session.add(_old_photo(1))
    await db_session.commit()

    assert await cleanup.cleanup_abandoned_photos() == 1

    assert await db_session.get(Photo, 1) is None
    assert s3_deletes == [("test-bucket", ["catio-bot/photos/submissions/1.jpg"])]


@pytest.mark.asyncio
async def test_cleanup_keeps_s3_object_when_photo_gets_a_post_after_scan(db_session, s3_deletes, monkeypatch):
    user = await get_or_create_user(db_session, telegram_id=123, full_name="User")
    db_session.add(_old_photo(1))
    await db_session.commit()

    calls = 0

    async def racing_get_abandoned_photos(session, older_than, **kwargs):
        nonlocal calls
        calls += 1
        result = await get_abandoned_photos(session, older_than, **kwargs)
        if calls == 1:
            # The user submits the photo right after the candidate scan.
            session.add(Post(user_id=user.id, file_id="f", status=PostStatus.APPROVED, photo_id=1))
            await session.commit()
        return result

    monkeypatch.setattr(cleanup, "get_abandoned_photos", racing_get_abandoned_photos)

    assert await cleanup.cleanup_abandoned_photos() == 0

    assert await db_session.get(Photo, 1) is not None
    assert s3_deletes == []
