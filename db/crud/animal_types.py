from dataclasses import dataclass

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.animal_type import AnimalType
from db.models.post import Post, PostStatus

LATIN_TO_CYRILLIC_HOMOGLYPHS = str.maketrans(
    {
        "A": "А",
        "B": "В",
        "C": "С",
        "E": "Е",
        "H": "Н",
        "K": "К",
        "M": "М",
        "O": "О",
        "P": "Р",
        "T": "Т",
        "X": "Х",
        "Y": "У",
        "a": "а",
        "c": "с",
        "e": "е",
        "k": "к",
        "m": "м",
        "o": "о",
        "p": "р",
        "t": "т",
        "x": "х",
        "y": "у",
    }
)


@dataclass(frozen=True)
class AnimalTypeOption:
    id: int
    name: str
    photo_count: int


POPULARITY_STATUSES = [PostStatus.APPROVED, PostStatus.PUBLISHED]


def _has_cyrillic(value: str) -> bool:
    return any("А" <= char <= "я" or char in "Ёё" for char in value)


def _has_latin(value: str) -> bool:
    return any("A" <= char <= "Z" or "a" <= char <= "z" for char in value)


def normalize_animal_type(value: str | None) -> str:
    if not value:
        return ""
    normalized = " ".join(value.split())
    if _has_cyrillic(normalized):
        normalized = normalized.translate(LATIN_TO_CYRILLIC_HOMOGLYPHS)
    return normalized


def animal_type_has_unsupported_latin(value: str | None) -> bool:
    return _has_latin(normalize_animal_type(value))


def is_valid_animal_type_name(value: str | None) -> bool:
    normalized = normalize_animal_type(value)
    return bool(normalized) and not animal_type_has_unsupported_latin(normalized)


def animal_type_lookup_key(value: str | None) -> str:
    return normalize_animal_type(value).replace("ё", "е").replace("Ё", "Е").casefold()


def is_cat_animal_type(value: str | None) -> bool:
    return animal_type_lookup_key(value) in CAT_ANIMAL_TYPE_KEYS


async def get_animal_type_options(session: AsyncSession, is_primary: bool) -> list[AnimalTypeOption]:
    photo_count = func.count(Post.id)
    stmt = (
        select(AnimalType.id, AnimalType.name, photo_count.label("photo_count"))
        .outerjoin(
            Post,
            and_(
                func.lower(Post.animal_type) == func.lower(AnimalType.name),
                Post.status.in_(POPULARITY_STATUSES),
            ),
        )
        .where(AnimalType.is_primary == is_primary)
        .group_by(AnimalType.id, AnimalType.name, AnimalType.sort_order)
        .order_by(photo_count.desc(), AnimalType.sort_order.asc(), AnimalType.name.asc())
    )
    result = await session.execute(stmt)
    return [
        AnimalTypeOption(id=animal_type_id, name=name, photo_count=photo_count_value)
        for animal_type_id, name, photo_count_value in result.all()
    ]


async def get_animal_type_name(session: AsyncSession, animal_type_id: int) -> str | None:
    stmt = select(AnimalType.name).where(AnimalType.id == animal_type_id)
    return await session.scalar(stmt)


async def _find_animal_type_by_normalized_name(session: AsyncSession, normalized: str) -> AnimalType | None:
    stmt = select(AnimalType).where(func.lower(AnimalType.name) == normalized.casefold())
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing:
        return existing

    lookup_key = animal_type_lookup_key(normalized)
    result = await session.execute(select(AnimalType))
    for animal_type in result.scalars():
        if animal_type_lookup_key(animal_type.name) == lookup_key:
            return animal_type
    return None


async def canonical_animal_type(session: AsyncSession, value: str | None) -> str:
    normalized = normalize_animal_type(value)
    if not normalized or animal_type_has_unsupported_latin(normalized):
        return ""

    existing = await _find_animal_type_by_normalized_name(session, normalized)
    if existing:
        existing_normalized = normalize_animal_type(existing.name)
        if existing_normalized and not animal_type_has_unsupported_latin(existing_normalized):
            return existing_normalized
        return existing.name
    return normalized


async def ensure_animal_type(session: AsyncSession, value: str | None, is_primary: bool = False) -> AnimalType | None:
    normalized = normalize_animal_type(value)
    if not normalized or animal_type_has_unsupported_latin(normalized):
        return None

    existing = await _find_animal_type_by_normalized_name(session, normalized)
    if existing:
        existing_normalized = normalize_animal_type(existing.name)
        if existing_normalized != existing.name and not animal_type_has_unsupported_latin(existing_normalized):
            duplicate = await session.scalar(
                select(AnimalType).where(
                    AnimalType.name == existing_normalized,
                    AnimalType.id != existing.id,
                )
            )
            if duplicate:
                return duplicate
            existing.name = existing_normalized
            await session.flush()
        return existing

    max_sort_order = await session.scalar(
        select(func.coalesce(func.max(AnimalType.sort_order), 0)).where(AnimalType.is_primary == is_primary)
    )
    animal_type = AnimalType(
        name=normalized,
        is_primary=is_primary,
        sort_order=(max_sort_order or 0) + 10,
    )
    session.add(animal_type)
    await session.flush()
    return animal_type


CAT_ANIMAL_TYPE_KEYS = {
    animal_type_lookup_key("кот"),
    animal_type_lookup_key("кошка"),
    animal_type_lookup_key("котик"),
    animal_type_lookup_key("котенок"),
    animal_type_lookup_key("котёнок"),
}
