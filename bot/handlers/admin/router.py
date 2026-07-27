from aiogram import Router
from aiogram.fsm.state import State, StatesGroup

admin_router = Router()


class AdminState(StatesGroup):
    waiting_for_reschedule_time = State()
    waiting_for_rejection_reason = State()
    waiting_for_custom_animal_type = State()
    waiting_for_broadcast_text = State()
    waiting_for_broadcast_confirm = State()


