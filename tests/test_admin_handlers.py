from types import SimpleNamespace

import pytest

from bot.handlers import admin
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


@pytest.mark.asyncio
async def test_admin_custom_animal_type_normalizes_homoglyphs(db_session, monkeypatch):
    monkeypatch.setattr(admin, "async_session", lambda: SessionContext(db_session))

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

    await admin.handle_admin_custom_animal_text(message, state, bot)
    await db_session.refresh(post)

    assert post.animal_type == "Насекомое"
    assert state.cleared
    assert message.answers[-1]["text"] == "Вид изменен."
    assert "Вид: Насекомое" in bot.edited_captions[-1]["caption"]
