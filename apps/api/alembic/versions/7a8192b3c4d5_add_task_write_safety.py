"""add task lease fencing and completion idempotency

Revision ID: 7a8192b3c4d5
Revises: 6f708192a3b4
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "7a8192b3c4d5"
down_revision: str | None = "6f708192a3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "generation_tasks",
        sa.Column("lease_token", sa.String(length=36), nullable=True),
    )
    op.create_index(
        "ix_generation_tasks_lease_token",
        "generation_tasks",
        ["lease_token"],
    )

    op.add_column(
        "assets",
        sa.Column("completion_key", sa.String(length=160), nullable=True),
    )
    op.add_column(
        "asset_candidates",
        sa.Column("completion_key", sa.String(length=160), nullable=True),
    )
    op.create_unique_constraint(
        "uq_assets_completion_key",
        "assets",
        ["completion_key"],
    )
    op.create_unique_constraint(
        "uq_asset_candidates_completion_key",
        "asset_candidates",
        ["completion_key"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_asset_candidates_completion_key",
        "asset_candidates",
        type_="unique",
    )
    op.drop_constraint(
        "uq_assets_completion_key",
        "assets",
        type_="unique",
    )
    op.drop_column("asset_candidates", "completion_key")
    op.drop_column("assets", "completion_key")

    op.drop_index(
        "ix_generation_tasks_lease_token",
        table_name="generation_tasks",
    )
    op.drop_column("generation_tasks", "lease_token")
