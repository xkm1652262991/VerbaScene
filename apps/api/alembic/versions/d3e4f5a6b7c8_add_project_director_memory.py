"""add project director memory

Revision ID: d3e4f5a6b7c8
Revises: 0f4a6b8c9d10
Create Date: 2026-06-10 13:20:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "d3e4f5a6b7c8"
down_revision: Union[str, None] = "0f4a6b8c9d10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "director_memory",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.alter_column("projects", "director_memory", server_default=None)


def downgrade() -> None:
    op.drop_column("projects", "director_memory")
