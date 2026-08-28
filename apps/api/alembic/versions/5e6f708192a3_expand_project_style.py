"""expand project style for descriptive visual prompts

Revision ID: 5e6f708192a3
Revises: 4d5e6f708192
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "5e6f708192a3"
down_revision: str | None = "4d5e6f708192"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "projects",
        "style",
        existing_type=sa.String(length=80),
        type_=sa.Text(),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "projects",
        "style",
        existing_type=sa.Text(),
        type_=sa.String(length=80),
        existing_nullable=False,
    )
