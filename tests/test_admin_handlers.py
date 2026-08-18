from datetime import date, datetime
from types import SimpleNamespace

import pytest

from bot.handlers.admin import callbacks as admin_callbacks
from bot.handlers.admin import helpers as admin_helpers
from db.crud.time_utils import app_timezone
from db.models.post import Post, PostStatus
from db.models.user import User


class SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakeState:
    def __init__(self, data):
        self.data = dict(data)
        self.cleared = False

    async def get_data(self):
        return dict(self.data)

    async def clear(self):
        self.cleared = True
        self.data.clear()


class FakeMessage:
    def __init__(self, text: str):
        self.text = text
        self.from_user = SimpleNamespace(id=1)
        self.chat = SimpleNamespace(id=1)
        self.answers = []

    async def answer(self, text, reply_markup=None):
        self.answers.append({"text": text, "reply_markup": reply_markup})


class FakeBot:
    def __init__(self):
        self.edited_captions = []

    async def edit_message_caption(self, **kwargs):
        self.edited_captions.append(kwargs)


def test_admin_schedule_text_includes_photo_command_and_author():
    post = SimpleNamespace(
        id=17,
        photo_id=88,
        animal_type="кот",
        schedule_time=datetime(2026, 7, 6, 18, 30, tzinfo=app_timezone()),
        status=PostStatus.APPROVED,
        user=SimpleNamespace(username="user", telegram_id=1001),
    )

    text = admin_helpers.admin_schedule_text(date(2026, 7, 6), [post])

    assert "/photo_88" in text
    assert "@user" in text


@pytest.mark.asyncio
async def test_admin_custom_animal_type_normalizes_homoglyphs(db_session, monkeypatch):
    monkeypatch.setattr(admin_callbacks, "async_session", lambda: SessionContext(db_session))

    user = User(telegram_id=1001, username="user", full_name="User")
    db_session.add(user)
    await db_session.flush()
    post = Post(user_id=user.id, file_id="file-id", animal_type="Кот", status=PostStatus.PENDING)
    db_session.add(post)
    await db_session.commit()

    state = FakeState(
        {
            "custom_animal_post_id": post.id,
            "custom_animal_message_chat_id": 1,
            "custom_animal_message_id": 10,
            "custom_animal_is_album_control": False,
            "custom_animal_is_album_view": False,
        }
    )
    message = FakeMessage("Нaceкомое")
    bot = FakeBot()

    from bot.handlers.admin import messages as admin_messages

    monkeypatch.setattr(admin_messages, "async_session", lambda: SessionContext(db_session))
    await admin_messages.handle_admin_custom_animal_text(message, state, bot)
    await db_session.refresh(post)

    assert post.animal_type == "Насекомое"
    assert state.cleared
    assert message.answers[-1]["text"] == "Вид изменен."
    assert "Вид: Насекомое" in bot.edited_captions[-1]["caption"]


@pytest.mark.asyncio
async def test_admin_pending_posts(db_session, monkeypatch):
    from bot.handlers.admin import actions as admin_actions
    from bot.handlers.admin import commands as admin_commands

    monkeypatch.setattr(admin_actions, "async_session", lambda: SessionContext(db_session))
    monkeypatch.setattr(admin_commands, "async_session", lambda: SessionContext(db_session))

    user = User(telegram_id=1001, username="testuser", full_name="Test User")
    db_session.add(user)
    await db_session.flush()

    post1 = Post(user_id=user.id, file_id="file1", animal_type="Кот", status=PostStatus.PENDING)
    post2 = Post(user_id=user.id, file_id="file2", animal_type="Собака", status=PostStatus.APPROVED)
    db_session.add_all([post1, post2])
    await db_session.commit()

    sent_photos = []

    class FakePendingBot:
        async def send_photo(self, chat_id, photo, caption, reply_markup=None):
            sent_photos.append({"photo": photo, "caption": caption, "reply_markup": reply_markup})

    bot = FakePendingBot()
    message = FakeMessage("/pending")

    await admin_commands.admin_pending_command(message, bot)

    assert len(message.answers) == 1
    assert "Найдено постов на модерации: 1" in message.answers[0]["text"]
    assert len(sent_photos) == 1
    assert sent_photos[0]["photo"] == "file1"
    assert "Вид: Кот" in sent_photos[0]["caption"]


@pytest.mark.asyncio
async def test_load_admin_stats_includes_tournament_completed_voters(db_session):
    from datetime import timedelta

    from db.models.photo import Photo
    from db.models.photo_tournament import (
        TOURNAMENT_RUNNING,
        TOURNAMENT_WEEKLY,
        PhotoTournament,
        PhotoTournamentEntry,
        PhotoTournamentMatch,
        PhotoTournamentRound,
        PhotoTournamentVote,
    )

    now = datetime(2026, 8, 18, 12, 0, tzinfo=app_timezone())
    tournament = PhotoTournament(
        type=TOURNAMENT_WEEKLY,
        status=TOURNAMENT_RUNNING,
        started_at=now - timedelta(days=1),
        voting_ends_at=now + timedelta(days=1),
        period_start=now - timedelta(days=7),
        period_end=now - timedelta(days=1),
        current_round_number=1,
    )
    db_session.add(tournament)
    await db_session.flush()

    round1 = PhotoTournamentRound(
        tournament_id=tournament.id,
        round_number=1,
        started_at=now,
        ends_at=now + timedelta(days=1),
    )
    round2 = PhotoTournamentRound(
        tournament_id=tournament.id,
        round_number=2,
        started_at=now,
        ends_at=now + timedelta(days=1),
    )
    db_session.add_all([round1, round2])
    await db_session.flush()

    match_r1 = PhotoTournamentMatch(tournament_id=tournament.id, round_id=round1.id, match_number=1)
    match_final = PhotoTournamentMatch(tournament_id=tournament.id, round_id=round2.id, match_number=1)
    db_session.add_all([match_r1, match_final])
    await db_session.flush()

    user1 = User(telegram_id=1, username="u1", full_name="U1")
    user2 = User(telegram_id=2, username="u2", full_name="U2")
    db_session.add_all([user1, user2])
    await db_session.flush()

    photo1 = Photo(storage_bucket="b", storage_key="1", sha256="1" * 64)
    photo2 = Photo(storage_bucket="b", storage_key="2", sha256="2" * 64)
    db_session.add_all([photo1, photo2])
    await db_session.flush()

    entry1 = PhotoTournamentEntry(tournament_id=tournament.id, photo_id=photo1.id, seed=1)
    entry2 = PhotoTournamentEntry(tournament_id=tournament.id, photo_id=photo2.id, seed=2)
    db_session.add_all([entry1, entry2])
    await db_session.flush()

    # User 1 voted in round 1 only
    v1 = PhotoTournamentVote(
        tournament_id=tournament.id,
        match_id=match_r1.id,
        user_id=user1.id,
        chosen_entry_id=entry1.id,
    )
    # User 2 voted in round 1 AND in final round
    v2_r1 = PhotoTournamentVote(
        tournament_id=tournament.id,
        match_id=match_r1.id,
        user_id=user2.id,
        chosen_entry_id=entry1.id,
    )
    v2_final = PhotoTournamentVote(
        tournament_id=tournament.id,
        match_id=match_final.id,
        user_id=user2.id,
        chosen_entry_id=entry2.id,
    )
    db_session.add_all([v1, v2_r1, v2_final])
    await db_session.commit()

    stats_text = await admin_helpers.load_admin_stats(db_session)
    assert "Проголосовало в текущем турнире: 2" in stats_text
    assert "Полностью прошли: 1" in stats_text
