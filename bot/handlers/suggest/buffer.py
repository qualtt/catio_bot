import asyncio
from dataclasses import dataclass

from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import Message


@dataclass
class AlbumBuffer:
    messages: list[Message]
    state: FSMContext
    bot: Bot
    task: asyncio.Task | None = None

_album_buffers: dict[tuple[int, str], AlbumBuffer] = {}

_single_submissions: dict[int, dict] = {}


_custom_animal_prompt_by_user: dict[int, int] = {}


def _get_single_submission(message_id: int) -> dict | None:
    return _single_submissions.get(message_id)


def _set_single_submission(message_id: int, data: dict) -> None:
    _single_submissions[message_id] = data


def _finish_single_submission(message_id: int) -> dict | None:
    submission = _single_submissions.pop(message_id, None)
    if submission is None:
        return None
    user_id = submission.get("user_id")
    if user_id is not None and _custom_animal_prompt_by_user.get(user_id) == message_id:
        _custom_animal_prompt_by_user.pop(user_id, None)
    return submission



__all__ = ['AlbumBuffer', '_album_buffers', '_custom_animal_prompt_by_user', '_finish_single_submission', '_get_single_submission', '_set_single_submission']
