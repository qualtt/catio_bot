import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InputMediaPhoto, Message, ReplyKeyboardRemove
from sqlalchemy import func, select

from bot.content import bot_content
from bot.keyboards.inline import get_tournament_match_kb, get_tournament_start_kb
from bot.services.tournaments import (
    TournamentMatchView,
    get_current_tournament,
    get_next_open_match_for_user,
    get_tournament,
    tournament_match_photo_input,
    tournament_period_label,
    tournament_type_label,
    tournament_voting_deadline_label,
    submit_tournament_vote,
)
from db.crud import get_or_create_user
from db.database import async_session
from db.models.photo_tournament import (
    TOURNAMENT_CANCELLED,
    TOURNAMENT_COMPLETED,
    TOURNAMENT_RUNNING,
    PhotoTournament,
    PhotoTournamentEntry,
)


tournament_router = Router()
logger = logging.getLogger(__name__)


def _tournament_status_label(status: str) -> str:
    if status == TOURNAMENT_COMPLETED:
        return bot_content.message("tournament_status_completed")
    if status == TOURNAMENT_CANCELLED:
        return bot_content.message("tournament_status_cancelled")
    return bot_content.message("tournament_status_running")


async def _tournament_status_text(session, tournament: PhotoTournament) -> str:
    entry_count = await session.scalar(
        select(func.count(PhotoTournamentEntry.id)).where(PhotoTournamentEntry.tournament_id == tournament.id)
    ) or 0
    return bot_content.message(
        "tournament_status",
        tournament_type=tournament_type_label(tournament.type),
        period=tournament_period_label(tournament),
        status=_tournament_status_label(tournament.status),
        round_number=tournament.current_round_number,
        entry_count=entry_count,
        voting_deadline=tournament_voting_deadline_label(tournament),
    )


async def _tournament_results_text(session, tournament: PhotoTournament) -> str:
    status_text = await _tournament_status_text(session, tournament)
    return bot_content.message(
        "tournament_results",
        status=status_text,
        winner_photo_id=tournament.winner_photo_id or "?",
        favorite_photo_id=tournament.favorite_photo_id or "?",
    )


def _match_caption(view: TournamentMatchView) -> str:
    match = view.match
    return bot_content.message(
        "tournament_match_caption",
        tournament_type=tournament_type_label(match.tournament.type),
        period=tournament_period_label(match.tournament),
        round_number=match.round.round_number,
        match_number=match.match_number,
        left_photo_id=view.left_entry.photo_id,
        right_photo_id=view.right_entry.photo_id,
        voting_deadline=tournament_voting_deadline_label(match.tournament),
    )


async def _show_status(
    bot: Bot,
    *,
    chat_id: int,
    source_message: Message | None,
    text: str,
    tournament_id: int | None = None,
) -> None:
    reply_markup = get_tournament_start_kb(tournament_id) if tournament_id else None
    if source_message and source_message.photo:
        try:
            await bot.edit_message_caption(
                chat_id=chat_id,
                message_id=source_message.message_id,
                caption=text,
                reply_markup=reply_markup,
            )
            return
        except TelegramAPIError:
            logger.exception("Failed to edit tournament status message %s", source_message.message_id)

    if source_message and source_message.text:
        try:
            await source_message.edit_text(text, reply_markup=reply_markup)
            return
        except TelegramAPIError:
            logger.exception("Failed to edit tournament text message %s", source_message.message_id)

    await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)


async def _send_or_edit_match(
    bot: Bot,
    *,
    chat_id: int,
    source_message: Message | None,
    view: TournamentMatchView,
) -> None:
    photo = await tournament_match_photo_input(view)
    caption = _match_caption(view)
    reply_markup = get_tournament_match_kb(
        view.match,
        left_entry_id=view.left_entry.id,
        right_entry_id=view.right_entry.id,
    )

    if source_message and source_message.photo:
        try:
            await bot.edit_message_media(
                chat_id=chat_id,
                message_id=source_message.message_id,
                media=InputMediaPhoto(media=photo, caption=caption),
                reply_markup=reply_markup,
            )
            return
        except TelegramAPIError:
            logger.exception("Failed to edit tournament match message %s", source_message.message_id)

    await bot.send_photo(
        chat_id=chat_id,
        photo=photo,
        caption=caption,
        reply_markup=reply_markup,
    )


async def _show_next_match(
    bot: Bot,
    *,
    chat_id: int,
    telegram_user,
    source_message: Message | None = None,
    tournament_id: int | None = None,
) -> None:
    async with async_session() as session:
        user = await get_or_create_user(
            session,
            telegram_id=telegram_user.id,
            username=telegram_user.username,
            full_name=telegram_user.full_name,
        )
        tournament = (
            await get_tournament(session, tournament_id)
            if tournament_id is not None
            else await get_current_tournament(session)
        )
        if tournament is not None and tournament.status == TOURNAMENT_COMPLETED:
            await _show_status(
                bot,
                chat_id=chat_id,
                source_message=source_message,
                text=await _tournament_results_text(session, tournament),
            )
            return

        view = await get_next_open_match_for_user(
            session,
            user_id=user.id,
            tournament_id=tournament_id,
        )
        if view is None:
            if tournament is None or tournament.status != TOURNAMENT_RUNNING:
                await _show_status(
                    bot,
                    chat_id=chat_id,
                    source_message=source_message,
                    text=bot_content.message("tournament_no_active"),
                )
                return

            status_text = await _tournament_status_text(session, tournament)
            await _show_status(
                bot,
                chat_id=chat_id,
                source_message=source_message,
                text=bot_content.message("tournament_voting_done", status=status_text),
                tournament_id=tournament.id,
            )
            return

    try:
        await _send_or_edit_match(bot, chat_id=chat_id, source_message=source_message, view=view)
    except Exception:
        logger.exception("Failed to send tournament match")
        await bot.send_message(
            chat_id=chat_id,
            text=bot_content.message("tournament_send_failed"),
            reply_markup=ReplyKeyboardRemove(),
        )


@tournament_router.message(Command("tournament"))
async def tournament_command(message: Message, state: FSMContext, bot: Bot):
    await state.clear()
    await _show_next_match(
        bot,
        chat_id=message.chat.id,
        telegram_user=message.from_user,
    )


@tournament_router.callback_query(F.data == "tourn_current")
async def handle_tournament_current(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await state.clear()
    await _show_next_match(
        bot,
        chat_id=callback.message.chat.id,
        telegram_user=callback.from_user,
        source_message=callback.message,
    )
    await callback.answer()


@tournament_router.callback_query(F.data.startswith("tourn_open_"))
async def handle_tournament_open(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await state.clear()
    try:
        tournament_id = int(callback.data.rsplit("_", 1)[1])
    except (TypeError, ValueError):
        await callback.answer(bot_content.message("tournament_not_found"), show_alert=True)
        return

    await _show_next_match(
        bot,
        chat_id=callback.message.chat.id,
        telegram_user=callback.from_user,
        source_message=callback.message,
        tournament_id=tournament_id,
    )
    await callback.answer()


@tournament_router.callback_query(F.data.startswith("tourn_vote_"))
async def handle_tournament_vote(callback: CallbackQuery, bot: Bot):
    try:
        _, _, match_id_raw, entry_id_raw = callback.data.split("_", 3)
        match_id = int(match_id_raw)
        entry_id = int(entry_id_raw)
    except (TypeError, ValueError):
        await callback.answer(bot_content.message("tournament_match_unavailable"), show_alert=True)
        return

    async with async_session() as session:
        user = await get_or_create_user(
            session,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            full_name=callback.from_user.full_name,
        )
        result = await submit_tournament_vote(
            session,
            match_id=match_id,
            chosen_entry_id=entry_id,
            user_id=user.id,
        )

    if not result.accepted:
        await callback.answer(bot_content.message("tournament_match_unavailable"), show_alert=True)
        return

    await callback.answer(
        bot_content.message("tournament_vote_recorded" if result.created else "tournament_already_voted")
    )
    await _show_next_match(
        bot,
        chat_id=callback.message.chat.id,
        telegram_user=callback.from_user,
        source_message=callback.message,
        tournament_id=result.tournament_id,
    )
