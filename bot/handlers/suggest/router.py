from aiogram import Router
from aiogram.filters import BaseFilter
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from .buffer import _custom_animal_prompt_by_user, _get_single_submission

suggest_router = Router()


class SuggestState(StatesGroup):
    waiting_for_animal_type = State()
    waiting_for_custom_animal_type = State()
    waiting_for_schedule_type = State()

class WaitingSingleCustomAnimalFilter(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        prompt_message_id = _custom_animal_prompt_by_user.get(message.from_user.id)
        if prompt_message_id is None:
            return False
        single = _get_single_submission(prompt_message_id)
        return single is not None and single.get("stage") == "custom_animal"

ALBUM_COLLECTION_DELAY_SECONDS = 1.0


