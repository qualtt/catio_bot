from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BufferedInputFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.content import bot_content
from bot.keyboards.inline import get_tournament_start_kb
from bot.services.photo_storage import _require_bucket, _s3_client, download_photo
from db.crud import now_in_app_tz
from db.models.photo import Photo
from db.models.photo_tournament import (
    NOTIFICATION_FAILED,
    NOTIFICATION_SENT,
    TOURNAMENT_COMPLETED,
    TOURNAMENT_MONTHLY,
    TOURNAMENT_RUNNING,
    PhotoTournament,
    PhotoTournamentEntry,
    PhotoTournamentNotification,
)
from db.models.user import User

from .bracket_drawer import generate_tournament_bracket_image
from .utils import (
    tournament_period_label,
    tournament_results_text,
    tournament_voting_deadline_label,
)

logger = logging.getLogger(__name__)


TOURNAMENT_RESULTS_SEND_DELAY_SECONDS = 0.05


async def send_tournament_notifications(
    bot: Bot,
    session: AsyncSession,
    tournament: PhotoTournament,
) -> int:
    if tournament.status != TOURNAMENT_RUNNING or tournament.notification_sent_at is not None:
        return 0

    entry_count = (
        await session.scalar(
            select(func.count(PhotoTournamentEntry.id)).where(PhotoTournamentEntry.tournament_id == tournament.id)
        )
        or 0
    )

    notified_user_ids = {
        user_id
        for user_id in (
            await session.execute(
                select(PhotoTournamentNotification.user_id).where(
                    PhotoTournamentNotification.tournament_id == tournament.id,
                )
            )
        ).scalars()
    }
    users = list((await session.execute(select(User).order_by(User.id.asc()))).scalars())
    sent_count = 0
    message_key = "tournament_monthly_invite" if tournament.type == TOURNAMENT_MONTHLY else "tournament_weekly_invite"

    for user in users:
        if user.id in notified_user_ids:
            continue
        try:
            sent = await bot.send_message(
                chat_id=user.telegram_id,
                text=bot_content.message(
                    message_key,
                    period=tournament_period_label(tournament),
                    count=entry_count,
                    voting_deadline=tournament_voting_deadline_label(tournament),
                ),
                reply_markup=get_tournament_start_kb(tournament.id),
            )
            notification = PhotoTournamentNotification(
                tournament_id=tournament.id,
                user_id=user.id,
                telegram_message_id=sent.message_id,
                status=NOTIFICATION_SENT,
            )
            sent_count += 1
        except TelegramAPIError as error:
            notification = PhotoTournamentNotification(
                tournament_id=tournament.id,
                user_id=user.id,
                status=NOTIFICATION_FAILED,
                error_message=str(error)[:500],
            )
        session.add(notification)

    tournament.notification_sent_at = now_in_app_tz()
    await session.commit()
    return sent_count


async def send_tournament_results_notifications(
    bot: Bot,
    session: AsyncSession,
    tournament: PhotoTournament,
) -> tuple[int, int]:
    if (
        tournament.status != TOURNAMENT_COMPLETED
        or tournament.results_notification_sent_at is not None
        or tournament.winner_photo_id is None
    ):
        return 0, 0

    text = await tournament_results_text(session, tournament)
    users = list((await session.execute(select(User).order_by(User.id.asc()))).scalars())
    sent_count = 0
    failed_count = 0
    # 1. Prepare winner photo
    photo = await session.get(Photo, tournament.winner_photo_id)
    if photo is None:
        logger.error(f"Winner photo {tournament.winner_photo_id} not found for tournament {tournament.id}")
        return 0, 0

    winner_photo_input = photo.telegram_file_id
    if not winner_photo_input:
        photo_data = await download_photo(storage_bucket=photo.storage_bucket, storage_key=photo.storage_key)
        winner_photo_input = BufferedInputFile(photo_data, filename=f"winner-{photo.id}.jpg")

    winner_photo_str = winner_photo_input if isinstance(winner_photo_input, str) else None

    # 2. Prepare bracket document
    bracket_bytes = await generate_tournament_bracket_image(session, tournament.id)
    bracket_input = None
    bracket_str = None
    if bracket_bytes:
        bracket_input = BufferedInputFile(bracket_bytes, filename=f"bracket-{tournament.id}.png")
        try:
            bucket = _require_bucket()
            key = f"tournaments/bracket_{tournament.id}.png"

            def upload():
                _s3_client().put_object(Bucket=bucket, Key=key, Body=bracket_bytes, ContentType="image/png")

            await asyncio.to_thread(upload)
        except Exception as e:
            logger.warning(f"Failed to upload bracket to S3: {e}")

    for user in users:
        try:
            # Send winner photo
            if not winner_photo_str and isinstance(winner_photo_input, BufferedInputFile):
                sent_msg = await bot.send_photo(
                    chat_id=user.telegram_id, photo=winner_photo_input, caption=text, request_timeout=300
                )
                if sent_msg.photo:
                    winner_photo_str = sent_msg.photo[-1].file_id
                    winner_photo_input = winner_photo_str
            else:
                await bot.send_photo(
                    chat_id=user.telegram_id, photo=winner_photo_input, caption=text, request_timeout=300
                )

            # Send bracket document if available
            if bracket_input:
                bracket_caption = bot_content.message("tournament_bracket_caption")
                if not bracket_str and isinstance(bracket_input, BufferedInputFile):
                    sent_doc = await bot.send_document(
                        chat_id=user.telegram_id, document=bracket_input, caption=bracket_caption, request_timeout=300
                    )
                    if sent_doc.document:
                        bracket_str = sent_doc.document.file_id
                        bracket_input = bracket_str
                else:
                    await bot.send_document(
                        chat_id=user.telegram_id, document=bracket_input, caption=bracket_caption, request_timeout=300
                    )

            sent_count += 1
        except TelegramAPIError as error:
            failed_count += 1
            logger.warning(
                "Tournament %s results notification failed for user %s: %s",
                tournament.id,
                user.id,
                error,
            )
        if TOURNAMENT_RESULTS_SEND_DELAY_SECONDS:
            await asyncio.sleep(TOURNAMENT_RESULTS_SEND_DELAY_SECONDS)

    tournament.results_notification_sent_at = now_in_app_tz()
    await session.commit()
    return sent_count, failed_count


async def send_pending_tournament_results_notifications(bot: Bot, session: AsyncSession) -> int:
    tournaments = list(
        (
            await session.execute(
                select(PhotoTournament)
                .where(
                    PhotoTournament.status == TOURNAMENT_COMPLETED,
                    PhotoTournament.results_notification_sent_at.is_(None),
                    PhotoTournament.winner_photo_id.is_not(None),
                )
                .order_by(PhotoTournament.completed_at.asc(), PhotoTournament.id.asc())
            )
        ).scalars()
    )
    notified_tournaments = 0
    for tournament in tournaments:
        sent_count, _ = await send_tournament_results_notifications(bot, session, tournament)
        if sent_count or tournament.results_notification_sent_at is not None:
            notified_tournaments += 1
            if sent_count:
                logger.info(
                    "Sent tournament %s results to %s users",
                    tournament.id,
                    sent_count,
                )
    return notified_tournaments
