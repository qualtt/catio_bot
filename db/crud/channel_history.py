from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.channel_history import ChannelHistory


async def get_channel_history_item_by_message_id(session: AsyncSession, message_id: int) -> ChannelHistory | None:
    stmt = select(ChannelHistory).where(ChannelHistory.message_id == message_id).order_by(ChannelHistory.id.asc()).limit(1)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_channel_history_item(
    session: AsyncSession,
    *,
    message_id: int,
    chat_id: int | None = None,
) -> ChannelHistory | None:
    if chat_id is not None:
        stmt = select(ChannelHistory).where(
            ChannelHistory.chat_id == chat_id,
            ChannelHistory.message_id == message_id,
        )
        item = (await session.execute(stmt)).scalar_one_or_none()
        if item is not None:
            return item

        stmt = select(ChannelHistory).where(
            ChannelHistory.chat_id.is_(None),
            ChannelHistory.message_id == message_id,
        )
        item = (await session.execute(stmt)).scalar_one_or_none()
        if item is not None:
            return item

    return await get_channel_history_item_by_message_id(session, message_id)


async def create_channel_history_item(
    session: AsyncSession,
    *,
    message_id: int,
    file_id: str,
    chat_id: int | None = None,
    photo_id: int | None = None,
    published_at: datetime | None = None,
    caption: str | None = None,
    media_group_id: str | int | None = None,
    animal_type: str | None = None,
    identified_by: int | None = None,
) -> ChannelHistory:
    normalized_media_group_id = str(media_group_id) if media_group_id is not None else None
    existing = await get_channel_history_item(session, message_id=message_id, chat_id=chat_id)
    if existing:
        if chat_id is not None:
            existing.chat_id = chat_id
        existing.file_id = file_id
        if photo_id is not None:
            existing.photo_id = photo_id
        if published_at is not None:
            existing.published_at = published_at
        existing.caption = caption
        existing.media_group_id = normalized_media_group_id
        if animal_type is not None:
            existing.animal_type = animal_type
        if identified_by is not None:
            existing.identified_by = identified_by
        await session.commit()
        await session.refresh(existing)
        return existing

    item = ChannelHistory(
        chat_id=chat_id,
        message_id=message_id,
        file_id=file_id,
        photo_id=photo_id,
        published_at=published_at,
        caption=caption,
        media_group_id=normalized_media_group_id,
        animal_type=animal_type,
        identified_by=identified_by,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


