"""Add animal types.

Revision ID: 7e2f4d9c8a1b
Revises: f3f6a1c7b8d9
Create Date: 2026-07-04 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7e2f4d9c8a1b"
down_revision: Union[str, None] = "f3f6a1c7b8d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


animal_types_table = sa.table(
    "animal_types",
    sa.column("name", sa.String(length=50)),
    sa.column("is_primary", sa.Boolean()),
    sa.column("sort_order", sa.Integer()),
)


def upgrade() -> None:
    op.create_table(
        "animal_types",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_animal_types_name"),
    )

    op.bulk_insert(
        animal_types_table,
        [
            {"name": "Кот", "is_primary": True, "sort_order": 10},
            {"name": "Собака", "is_primary": True, "sort_order": 20},
            {"name": "Крыса", "is_primary": False, "sort_order": 10},
            {"name": "Птица", "is_primary": False, "sort_order": 20},
            {"name": "Хомяк", "is_primary": False, "sort_order": 30},
            {"name": "Кролик", "is_primary": False, "sort_order": 40},
            {"name": "Рыба", "is_primary": False, "sort_order": 50},
            {"name": "Рептилия", "is_primary": False, "sort_order": 60},
        ],
    )

    op.execute(
        """
        INSERT INTO animal_types (name, is_primary, sort_order, created_at)
        SELECT animal_type, false, 1000 + row_number() OVER (ORDER BY lower(animal_type)) * 10, now()
        FROM (
            SELECT DISTINCT ON (lower(btrim(animal_type)))
                btrim(animal_type) AS animal_type
            FROM posts
            WHERE animal_type IS NOT NULL
                AND btrim(animal_type) != ''
                AND status IN ('APPROVED', 'PUBLISHED')
            ORDER BY lower(btrim(animal_type)), btrim(animal_type)
        ) existing_types
        WHERE NOT EXISTS (
            SELECT 1
            FROM animal_types
            WHERE lower(animal_types.name) = lower(existing_types.animal_type)
        )
        """
    )


def downgrade() -> None:
    op.drop_table("animal_types")
