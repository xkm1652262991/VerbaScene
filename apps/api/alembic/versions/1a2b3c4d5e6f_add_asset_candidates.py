"""add asset candidates

Revision ID: 1a2b3c4d5e6f
Revises: f1a2b3c4d5e6
Create Date: 2026-06-17 11:50:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "1a2b3c4d5e6f"
down_revision: str | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "asset_candidates",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("asset_id", sa.String(length=36), nullable=True),
        sa.Column("source_task_id", sa.String(length=36), nullable=True),
        sa.Column("source_stage_run_id", sa.String(length=36), nullable=True),
        sa.Column("source_script_id", sa.String(length=36), nullable=True),
        sa.Column("source_shot_batch_id", sa.String(length=36), nullable=True),
        sa.Column("candidate_type", sa.String(length=40), nullable=False),
        sa.Column("asset_type", sa.String(length=40), nullable=False),
        sa.Column("asset_role", sa.String(length=80), nullable=True),
        sa.Column("entity_type", sa.String(length=80), nullable=True),
        sa.Column("entity_id", sa.String(length=36), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("uri", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.String(length=120), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("duration_sec", sa.Numeric(10, 3), nullable=True),
        sa.Column("provider", sa.String(length=120), nullable=True),
        sa.Column("model", sa.String(length=160), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=True),
        sa.Column("negative_prompt", sa.Text(), nullable=True),
        sa.Column("raw_response", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("promoted_asset_id", sa.String(length=36), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["promoted_asset_id"], ["assets.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_asset_candidates_asset_id"), "asset_candidates", ["asset_id"], unique=False)
    op.create_index(op.f("ix_asset_candidates_asset_role"), "asset_candidates", ["asset_role"], unique=False)
    op.create_index(op.f("ix_asset_candidates_asset_type"), "asset_candidates", ["asset_type"], unique=False)
    op.create_index(op.f("ix_asset_candidates_candidate_type"), "asset_candidates", ["candidate_type"], unique=False)
    op.create_index(op.f("ix_asset_candidates_entity_id"), "asset_candidates", ["entity_id"], unique=False)
    op.create_index(op.f("ix_asset_candidates_entity_type"), "asset_candidates", ["entity_type"], unique=False)
    op.create_index(op.f("ix_asset_candidates_project_id"), "asset_candidates", ["project_id"], unique=False)
    op.create_index(op.f("ix_asset_candidates_promoted_asset_id"), "asset_candidates", ["promoted_asset_id"], unique=False)
    op.create_index(op.f("ix_asset_candidates_provider"), "asset_candidates", ["provider"], unique=False)
    op.create_index(op.f("ix_asset_candidates_source_script_id"), "asset_candidates", ["source_script_id"], unique=False)
    op.create_index(op.f("ix_asset_candidates_source_shot_batch_id"), "asset_candidates", ["source_shot_batch_id"], unique=False)
    op.create_index(op.f("ix_asset_candidates_source_stage_run_id"), "asset_candidates", ["source_stage_run_id"], unique=False)
    op.create_index(op.f("ix_asset_candidates_source_task_id"), "asset_candidates", ["source_task_id"], unique=False)
    op.create_index(op.f("ix_asset_candidates_status"), "asset_candidates", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_asset_candidates_status"), table_name="asset_candidates")
    op.drop_index(op.f("ix_asset_candidates_source_task_id"), table_name="asset_candidates")
    op.drop_index(op.f("ix_asset_candidates_source_stage_run_id"), table_name="asset_candidates")
    op.drop_index(op.f("ix_asset_candidates_source_shot_batch_id"), table_name="asset_candidates")
    op.drop_index(op.f("ix_asset_candidates_source_script_id"), table_name="asset_candidates")
    op.drop_index(op.f("ix_asset_candidates_provider"), table_name="asset_candidates")
    op.drop_index(op.f("ix_asset_candidates_promoted_asset_id"), table_name="asset_candidates")
    op.drop_index(op.f("ix_asset_candidates_project_id"), table_name="asset_candidates")
    op.drop_index(op.f("ix_asset_candidates_entity_type"), table_name="asset_candidates")
    op.drop_index(op.f("ix_asset_candidates_entity_id"), table_name="asset_candidates")
    op.drop_index(op.f("ix_asset_candidates_candidate_type"), table_name="asset_candidates")
    op.drop_index(op.f("ix_asset_candidates_asset_type"), table_name="asset_candidates")
    op.drop_index(op.f("ix_asset_candidates_asset_role"), table_name="asset_candidates")
    op.drop_index(op.f("ix_asset_candidates_asset_id"), table_name="asset_candidates")
    op.drop_table("asset_candidates")
