from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.config import config
from bot.services.scoring import award_old_photo_identification_score
from db.crud import ensure_animal_type, now_in_app_tz
from db.models.channel_history import ChannelHistory
from db.models.photo_identification import (
    PhotoIdentificationAssignment,
    PhotoIdentificationBatch,
    PhotoIdentificationBatchItem,
    PhotoIdentificationVote,
)


ASSIGNMENT_ASSIGNED = "assigned"
ASSIGNMENT_ANSWERED = "answered"
ASSIGNMENT_EXPIRED = "expired"

REVIEW_OPEN = "open"
REVIEW_QUEUED = "queued"
REVIEW_SENT = "sent"
REVIEW_APPROVED = "approved"

BATCH_PENDING = "pending"
BATCH_COMPLETED = "completed"

ITEM_PENDING = "pending"
ITEM_APPROVED = "approved"
ITEM_REJECTED = "rejected"


@dataclass(frozen=True)
class VoteResult:
    vote: PhotoIdentificationVote | None
    created: bool
    queued_for_review: bool


@dataclass(frozen=True)
class BatchFinalization:
    approved_count: int
    rejected_count: int
    awarded_points: int


async def expire_identification_assignments(session: AsyncSession) -> None:
    now = now_in_app_tz()
    result = await session.execute(
        select(PhotoIdentificationAssignment).where(
            PhotoIdentificationAssignment.status == ASSIGNMENT_ASSIGNED,
            PhotoIdentificationAssignment.expires_at <= now,
        )
    )
    for assignment in result.scalars():
        assignment.status = ASSIGNMENT_EXPIRED
    await session.flush()


async def get_active_identification_assignment(
    session: AsyncSession,
    user_id: int,
) -> PhotoIdentificationAssignment | None:
    await expire_identification_assignments(session)
    now = now_in_app_tz()
    stmt = (
        select(PhotoIdentificationAssignment)
        .options(
            selectinload(PhotoIdentificationAssignment.channel_history).selectinload(ChannelHistory.photo)
        )
        .where(
            PhotoIdentificationAssignment.user_id == user_id,
            PhotoIdentificationAssignment.status == ASSIGNMENT_ASSIGNED,
            PhotoIdentificationAssignment.expires_at > now,
        )
        .order_by(PhotoIdentificationAssignment.created_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def assign_next_identification_item(
    session: AsyncSession,
    user_id: int,
) -> PhotoIdentificationAssignment | None:
    existing = await get_active_identification_assignment(session, user_id)
    if existing:
        return existing

    now = now_in_app_tz()
    pending_vote_count = (
        select(func.count(PhotoIdentificationVote.id))
        .where(
            PhotoIdentificationVote.channel_history_id == ChannelHistory.id,
            PhotoIdentificationVote.reviewed_at.is_(None),
        )
        .correlate(ChannelHistory)
        .scalar_subquery()
    )
    active_assignment_count = (
        select(func.count(PhotoIdentificationAssignment.id))
        .where(
            PhotoIdentificationAssignment.channel_history_id == ChannelHistory.id,
            PhotoIdentificationAssignment.status == ASSIGNMENT_ASSIGNED,
            PhotoIdentificationAssignment.expires_at > now,
        )
        .correlate(ChannelHistory)
        .scalar_subquery()
    )
    user_vote_exists = (
        select(PhotoIdentificationVote.id)
        .where(
            PhotoIdentificationVote.channel_history_id == ChannelHistory.id,
            PhotoIdentificationVote.user_id == user_id,
        )
        .exists()
    )
    user_assignment_exists = (
        select(PhotoIdentificationAssignment.id)
        .where(
            PhotoIdentificationAssignment.channel_history_id == ChannelHistory.id,
            PhotoIdentificationAssignment.user_id == user_id,
        )
        .exists()
    )

    stmt = (
        select(ChannelHistory)
        .options(selectinload(ChannelHistory.photo))
        .where(
            ChannelHistory.photo_id.is_not(None),
            ChannelHistory.animal_type.is_(None),
            or_(ChannelHistory.review_status.is_(None), ChannelHistory.review_status == REVIEW_OPEN),
            ~user_vote_exists,
            ~user_assignment_exists,
            pending_vote_count < max(config.IDENTIFICATION_MAX_VOTES_PER_PHOTO, config.IDENTIFICATION_VOTES_REQUIRED, 1),
            active_assignment_count < max(config.IDENTIFICATION_MAX_ACTIVE_ASSIGNMENTS_PER_PHOTO, 1),
        )
        .order_by(pending_vote_count.desc(), ChannelHistory.id.asc())
        .limit(1)
    )
    channel_history = (await session.execute(stmt)).scalar_one_or_none()
    if channel_history is None:
        await session.commit()
        return None

    assignment = PhotoIdentificationAssignment(
        channel_history=channel_history,
        user_id=user_id,
        status=ASSIGNMENT_ASSIGNED,
        expires_at=now + timedelta(minutes=max(config.IDENTIFICATION_ASSIGNMENT_TTL_MINUTES, 1)),
    )
    session.add(assignment)
    await session.commit()
    await session.refresh(assignment)
    assignment.channel_history = channel_history
    return assignment


async def queue_identification_item_if_ready(
    session: AsyncSession,
    channel_history_id: int,
) -> bool:
    channel_history = await session.get(ChannelHistory, channel_history_id)
    if (
        channel_history is None
        or channel_history.animal_type is not None
        or channel_history.review_status not in (None, REVIEW_OPEN)
    ):
        return False

    result = await session.execute(
        select(PhotoIdentificationVote.animal_type, func.count(PhotoIdentificationVote.id))
        .where(
            PhotoIdentificationVote.channel_history_id == channel_history_id,
            PhotoIdentificationVote.reviewed_at.is_(None),
        )
        .group_by(PhotoIdentificationVote.animal_type)
        .order_by(func.count(PhotoIdentificationVote.id).desc())
    )
    rows = result.all()
    if not rows:
        return False

    total_votes = sum(count for _, count in rows)
    top_animal_type, top_votes = rows[0]
    votes_required = max(config.IDENTIFICATION_VOTES_REQUIRED, 1)
    consensus_percent = max(config.IDENTIFICATION_CONSENSUS_PERCENT, 1)
    max_votes = max(config.IDENTIFICATION_MAX_VOTES_PER_PHOTO, votes_required)
    has_consensus = top_votes >= votes_required and top_votes * 100 >= total_votes * consensus_percent
    max_votes_reached = total_votes >= max_votes
    if not has_consensus and not max_votes_reached:
        return False

    channel_history.review_status = REVIEW_QUEUED
    channel_history.suggested_animal_type = top_animal_type
    await session.flush()
    return True


async def queue_ready_identification_items(session: AsyncSession, *, limit: int = 500) -> int:
    stmt = (
        select(ChannelHistory.id)
        .join(PhotoIdentificationVote, PhotoIdentificationVote.channel_history_id == ChannelHistory.id)
        .where(
            ChannelHistory.photo_id.is_not(None),
            ChannelHistory.animal_type.is_(None),
            or_(ChannelHistory.review_status.is_(None), ChannelHistory.review_status == REVIEW_OPEN),
            PhotoIdentificationVote.reviewed_at.is_(None),
        )
        .group_by(ChannelHistory.id)
        .order_by(ChannelHistory.id.asc())
        .limit(limit)
    )
    result = await session.execute(stmt)

    queued_count = 0
    for channel_history_id in result.scalars():
        if await queue_identification_item_if_ready(session, channel_history_id):
            queued_count += 1

    return queued_count


async def submit_identification_vote(
    session: AsyncSession,
    *,
    user_id: int,
    animal_type: str,
) -> VoteResult:
    assignment = await get_active_identification_assignment(session, user_id)
    if assignment is None:
        await session.commit()
        return VoteResult(vote=None, created=False, queued_for_review=False)

    if assignment.channel_history.animal_type is not None or assignment.channel_history.review_status == REVIEW_APPROVED:
        assignment.status = ASSIGNMENT_EXPIRED
        await session.commit()
        return VoteResult(vote=None, created=False, queued_for_review=False)

    existing_vote = await session.scalar(
        select(PhotoIdentificationVote).where(
            PhotoIdentificationVote.channel_history_id == assignment.channel_history_id,
            PhotoIdentificationVote.user_id == user_id,
        )
    )
    if existing_vote:
        assignment.status = ASSIGNMENT_ANSWERED
        assignment.answered_at = now_in_app_tz()
        await session.commit()
        return VoteResult(vote=existing_vote, created=False, queued_for_review=False)

    now = now_in_app_tz()
    vote = PhotoIdentificationVote(
        channel_history_id=assignment.channel_history_id,
        user_id=user_id,
        animal_type=animal_type,
    )
    assignment.status = ASSIGNMENT_ANSWERED
    assignment.answered_at = now
    session.add(vote)
    await session.flush()
    queued_for_review = await queue_identification_item_if_ready(session, assignment.channel_history_id)
    await session.commit()
    await session.refresh(vote)
    return VoteResult(vote=vote, created=True, queued_for_review=queued_for_review)


async def create_ready_identification_batches(
    session: AsyncSession,
    *,
    min_size: int | None = None,
    max_batches: int = 5,
) -> list[int]:
    await queue_ready_identification_items(session)

    min_size = max(min_size if min_size is not None else config.IDENTIFICATION_BATCH_SIZE, 1)
    batch_size = max(config.IDENTIFICATION_BATCH_SIZE, 1)
    result = await session.execute(
        select(ChannelHistory.suggested_animal_type, func.count(ChannelHistory.id))
        .where(
            ChannelHistory.review_status == REVIEW_QUEUED,
            ChannelHistory.review_sent_at.is_(None),
            ChannelHistory.suggested_animal_type.is_not(None),
        )
        .group_by(ChannelHistory.suggested_animal_type)
        .having(func.count(ChannelHistory.id) >= min_size)
        .order_by(func.count(ChannelHistory.id).desc(), ChannelHistory.suggested_animal_type.asc())
        .limit(max_batches)
    )

    batch_ids: list[int] = []
    now = now_in_app_tz()
    for animal_type, _ in result.all():
        item_result = await session.execute(
            select(ChannelHistory)
            .where(
                ChannelHistory.review_status == REVIEW_QUEUED,
                ChannelHistory.review_sent_at.is_(None),
                ChannelHistory.suggested_animal_type == animal_type,
            )
            .order_by(ChannelHistory.id.asc())
            .limit(batch_size)
        )
        channel_items = list(item_result.scalars())
        if len(channel_items) < min_size:
            continue

        batch = PhotoIdentificationBatch(
            animal_type=animal_type,
            status=BATCH_PENDING,
        )
        session.add(batch)
        await session.flush()

        for index, channel_history in enumerate(channel_items, start=1):
            session.add(
                PhotoIdentificationBatchItem(
                    batch_id=batch.id,
                    channel_history_id=channel_history.id,
                    item_number=index,
                    status=ITEM_PENDING,
                )
            )
            channel_history.review_status = REVIEW_SENT
            channel_history.review_sent_at = now

        await session.flush()
        batch_ids.append(batch.id)

    await session.commit()
    return batch_ids


async def get_identification_batch(
    session: AsyncSession,
    batch_id: int,
) -> PhotoIdentificationBatch | None:
    stmt = (
        select(PhotoIdentificationBatch)
        .options(
            selectinload(PhotoIdentificationBatch.items)
            .selectinload(PhotoIdentificationBatchItem.channel_history)
            .selectinload(ChannelHistory.photo)
        )
        .where(PhotoIdentificationBatch.id == batch_id)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_unsent_identification_batches(
    session: AsyncSession,
    *,
    limit: int = 5,
) -> list[PhotoIdentificationBatch]:
    stmt = (
        select(PhotoIdentificationBatch)
        .options(
            selectinload(PhotoIdentificationBatch.items)
            .selectinload(PhotoIdentificationBatchItem.channel_history)
            .selectinload(ChannelHistory.photo)
        )
        .where(
            PhotoIdentificationBatch.status == BATCH_PENDING,
            PhotoIdentificationBatch.sent_at.is_(None),
        )
        .order_by(PhotoIdentificationBatch.created_at.asc(), PhotoIdentificationBatch.id.asc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars())


async def mark_identification_batch_sent(
    session: AsyncSession,
    *,
    batch_id: int,
    control_message_id: int,
) -> None:
    batch = await session.get(PhotoIdentificationBatch, batch_id)
    if batch is None:
        return
    batch.sent_at = now_in_app_tz()
    batch.control_message_id = control_message_id
    await session.commit()


async def toggle_identification_batch_item(
    session: AsyncSession,
    *,
    batch_id: int,
    item_number: int,
) -> PhotoIdentificationBatch | None:
    batch = await get_identification_batch(session, batch_id)
    if batch is None or batch.status != BATCH_PENDING:
        return batch

    for item in batch.items:
        if item.item_number == item_number:
            item.status = ITEM_PENDING if item.status == ITEM_REJECTED else ITEM_REJECTED
            await session.commit()
            return await get_identification_batch(session, batch_id)
    return batch


async def _approve_identification_item(
    session: AsyncSession,
    *,
    channel_history: ChannelHistory,
    animal_type: str,
) -> int:
    now = now_in_app_tz()
    await ensure_animal_type(session, animal_type)
    channel_history.animal_type = animal_type
    channel_history.suggested_animal_type = animal_type
    channel_history.review_status = REVIEW_APPROVED
    channel_history.reviewed_at = now

    result = await session.execute(
        select(PhotoIdentificationVote).where(
            PhotoIdentificationVote.channel_history_id == channel_history.id,
            PhotoIdentificationVote.reviewed_at.is_(None),
        )
    )
    awarded_points = 0
    first_correct_user_id: int | None = None
    for vote in result.scalars():
        is_correct = vote.animal_type.casefold() == animal_type.casefold()
        vote.is_correct = is_correct
        vote.reviewed_at = now
        if is_correct:
            first_correct_user_id = first_correct_user_id or vote.user_id
            award = await award_old_photo_identification_score(
                session,
                user_id=vote.user_id,
                channel_history_id=channel_history.id,
                animal_type=animal_type,
            )
            awarded_points += award.points if award.created else 0

    if first_correct_user_id is not None:
        channel_history.identified_by = first_correct_user_id

    return awarded_points


async def _reject_identification_item(
    session: AsyncSession,
    *,
    channel_history: ChannelHistory,
) -> None:
    now = now_in_app_tz()
    channel_history.suggested_animal_type = None
    channel_history.review_status = REVIEW_OPEN
    channel_history.review_sent_at = None
    channel_history.reviewed_at = None

    result = await session.execute(
        select(PhotoIdentificationVote).where(
            PhotoIdentificationVote.channel_history_id == channel_history.id,
            PhotoIdentificationVote.reviewed_at.is_(None),
        )
    )
    for vote in result.scalars():
        vote.is_correct = False
        vote.reviewed_at = now


async def finalize_identification_batch(
    session: AsyncSession,
    *,
    batch_id: int,
    reject_all: bool = False,
) -> BatchFinalization | None:
    batch = await get_identification_batch(session, batch_id)
    if batch is None or batch.status != BATCH_PENDING:
        return None

    approved_count = 0
    rejected_count = 0
    awarded_points = 0
    for item in batch.items:
        if reject_all or item.status == ITEM_REJECTED:
            await _reject_identification_item(session, channel_history=item.channel_history)
            item.status = ITEM_REJECTED
            rejected_count += 1
        else:
            awarded_points += await _approve_identification_item(
                session,
                channel_history=item.channel_history,
                animal_type=batch.animal_type,
            )
            item.status = ITEM_APPROVED
            approved_count += 1

    batch.status = BATCH_COMPLETED
    batch.completed_at = now_in_app_tz()
    await session.commit()
    return BatchFinalization(
        approved_count=approved_count,
        rejected_count=rejected_count,
        awarded_points=awarded_points,
    )
