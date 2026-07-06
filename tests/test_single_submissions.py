from types import SimpleNamespace

import pytest

from bot.handlers import suggest


class FakeReplyMessage:
    def __init__(self, message_id: int):
        self.message_id = message_id
        self.chat = SimpleNamespace(id=1001)
        self.photo = True

    async def edit_caption(self, **kwargs):
        self.edited = kwargs

    async def edit_text(self, **kwargs):
        self.edited = kwargs


class FakeCallback:
    def __init__(self, message_id: int):
        self.message = FakeReplyMessage(message_id)
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append({"text": text, "show_alert": show_alert})


@pytest.fixture(autouse=True)
def clear_single_submissions():
    suggest._single_submissions.clear()
    suggest._custom_animal_prompt_by_user.clear()
    yield
    suggest._single_submissions.clear()
    suggest._custom_animal_prompt_by_user.clear()


def test_single_submissions_are_keyed_by_prompt_message_id():
    suggest._set_single_submission(101, {"file_id": "first", "photo_id": 1})
    suggest._set_single_submission(202, {"file_id": "last", "photo_id": 2})

    assert suggest._get_single_submission(101)["file_id"] == "first"
    assert suggest._get_single_submission(202)["file_id"] == "last"


@pytest.mark.asyncio
async def test_select_animal_type_uses_matching_prompt_message():
    suggest._set_single_submission(
        101,
        {
            "file_id": "first",
            "photo_id": 1,
            "user_id": 7,
            "stage": "animal",
        },
    )
    suggest._set_single_submission(
        202,
        {
            "file_id": "last",
            "photo_id": 2,
            "user_id": 7,
            "stage": "animal",
        },
    )

    callback = FakeCallback(101)
    state = SimpleNamespace()

    async def get_data():
        return {}

    state.get_data = get_data

    await suggest.select_animal_type(callback, state, bot=None, animal_type="кот")

    assert suggest._get_single_submission(101)["animal_type"] == "кот"
    assert suggest._get_single_submission(202)["animal_type"] is None
    assert callback.message.edited["caption"].startswith("Животное: кот.")


@pytest.mark.asyncio
async def test_finish_single_submission_removes_only_target_entry():
    suggest._set_single_submission(101, {"file_id": "first", "user_id": 7})
    suggest._set_single_submission(202, {"file_id": "last", "user_id": 7})
    suggest._custom_animal_prompt_by_user[7] = 101

    finished = suggest._finish_single_submission(101)

    assert finished["file_id"] == "first"
    assert suggest._get_single_submission(101) is None
    assert suggest._get_single_submission(202)["file_id"] == "last"
    assert 7 not in suggest._custom_animal_prompt_by_user
