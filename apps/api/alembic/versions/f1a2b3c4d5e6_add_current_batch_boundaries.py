"""add current batch boundaries

Revision ID: f1a2b3c4d5e6
Revises: e0f1a2b3c4d5
Create Date: 2026-06-16 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "f1a2b3c4d5e6"
down_revision: str | None = "e0f1a2b3c4d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("shots", sa.Column("shot_batch_id", sa.String(length=36), nullable=True))
    op.add_column("shots", sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.create_index(op.f("ix_shots_shot_batch_id"), "shots", ["shot_batch_id"], unique=False)
    op.create_index(op.f("ix_shots_is_current"), "shots", ["is_current"], unique=False)
    op.alter_column("shots", "is_current", server_default=None)

    op.add_column("assets", sa.Column("source_task_id", sa.String(length=36), nullable=True))
    op.add_column("assets", sa.Column("source_stage_run_id", sa.String(length=36), nullable=True))
    op.add_column("assets", sa.Column("source_script_id", sa.String(length=36), nullable=True))
    op.add_column("assets", sa.Column("source_shot_batch_id", sa.String(length=36), nullable=True))
    op.create_index(op.f("ix_assets_source_task_id"), "assets", ["source_task_id"], unique=False)
    op.create_index(op.f("ix_assets_source_stage_run_id"), "assets", ["source_stage_run_id"], unique=False)
    op.create_index(op.f("ix_assets_source_script_id"), "assets", ["source_script_id"], unique=False)
    op.create_index(op.f("ix_assets_source_shot_batch_id"), "assets", ["source_shot_batch_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_assets_source_shot_batch_id"), table_name="assets")
    op.drop_index(op.f("ix_assets_source_script_id"), table_name="assets")
    op.drop_index(op.f("ix_assets_source_stage_run_id"), table_name="assets")
    op.drop_index(op.f("ix_assets_source_task_id"), table_name="assets")
    op.drop_column("assets", "source_shot_batch_id")
    op.drop_column("assets", "source_script_id")
    op.drop_column("assets", "source_stage_run_id")
    op.drop_column("assets", "source_task_id")

    op.drop_index(op.f("ix_shots_is_current"), table_name="shots")
    op.drop_index(op.f("ix_shots_shot_batch_id"), table_name="shots")
    op.drop_column("shots", "is_current")
    op.drop_column("shots", "shot_batch_id")
