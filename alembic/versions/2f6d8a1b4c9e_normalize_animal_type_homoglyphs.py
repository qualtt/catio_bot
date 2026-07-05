"""Normalize animal type homoglyphs.

Revision ID: 2f6d8a1b4c9e
Revises: 9a7c5d2e1f0b
Create Date: 2026-07-06 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "2f6d8a1b4c9e"
down_revision: Union[str, None] = "9a7c5d2e1f0b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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


TEXT_COLUMNS = (
    ("posts", "animal_type"),
    ("channel_history", "animal_type"),
    ("channel_history", "suggested_animal_type"),
    ("photo_identification_votes", "animal_type"),
    ("photo_identification_batches", "animal_type"),
)


def _has_cyrillic(value: str) -> bool:
    return any("А" <= char <= "я" or char in "Ёё" for char in value)


def _has_latin(value: str) -> bool:
    return any("A" <= char <= "Z" or "a" <= char <= "z" for char in value)


def _normalize(value: str | None) -> str:
    if not value:
        return ""
    normalized = " ".join(value.split())
    if _has_cyrillic(normalized):
        normalized = normalized.translate(LATIN_TO_CYRILLIC_HOMOGLYPHS)
    return normalized


def _lookup_key(value: str | None) -> str:
    return _normalize(value).replace("ё", "е").replace("Ё", "Е").casefold()


def _safe_target(value: str | None, canonical_by_key: dict[str, str]) -> str | None:
    normalized = _normalize(value)
    if not normalized or _has_latin(normalized):
        return None
    return canonical_by_key.get(_lookup_key(normalized), normalized)


def _replace_value(connection, table_name: str, column_name: str, old_value: str, new_value: str) -> None:
    connection.execute(
        sa.text(f"UPDATE {table_name} SET {column_name} = :new_value WHERE {column_name} = :old_value"),
        {"old_value": old_value, "new_value": new_value},
    )


def upgrade() -> None:
    connection = op.get_bind()

    animal_type_rows = list(
        connection.execute(sa.text("SELECT id, name FROM animal_types ORDER BY id")).mappings()
    )
    canonical_by_key: dict[str, str] = {}
    for row in animal_type_rows:
        target = _safe_target(row["name"], {})
        if target is None:
            continue
        canonical_by_key.setdefault(_lookup_key(target), target)

    for row in animal_type_rows:
        old_name = row["name"]
        target = _safe_target(old_name, canonical_by_key)
        if target is None or target == old_name:
            continue

        for table_name, column_name in TEXT_COLUMNS:
            _replace_value(connection, table_name, column_name, old_name, target)

        existing_id = connection.scalar(
            sa.text("SELECT id FROM animal_types WHERE name = :target AND id != :id"),
            {"target": target, "id": row["id"]},
        )
        if existing_id is not None:
            connection.execute(sa.text("DELETE FROM animal_types WHERE id = :id"), {"id": row["id"]})
        else:
            connection.execute(
                sa.text("UPDATE animal_types SET name = :target WHERE id = :id"),
                {"target": target, "id": row["id"]},
            )
        canonical_by_key[_lookup_key(target)] = target

    for table_name, column_name in TEXT_COLUMNS:
        values = list(
            connection.execute(
                sa.text(f"SELECT DISTINCT {column_name} AS value FROM {table_name} WHERE {column_name} IS NOT NULL")
            ).scalars()
        )
        for value in values:
            target = _safe_target(value, canonical_by_key)
            if target is not None and target != value:
                _replace_value(connection, table_name, column_name, value, target)


def downgrade() -> None:
    pass
