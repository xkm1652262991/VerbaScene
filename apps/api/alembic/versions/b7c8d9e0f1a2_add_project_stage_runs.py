"""add project stage runs"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "b7c8d9e0f1a2"
down_revision: str | None = "a6b7c8d9e0f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project_stage_runs",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("stage", sa.String(length=80), nullable=False),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("input_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("output_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("run_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_project_stage_runs_action"), "project_stage_runs", ["action"], unique=False)
    op.create_index(op.f("ix_project_stage_runs_project_id"), "project_stage_runs", ["project_id"], unique=False)
    op.create_index(op.f("ix_project_stage_runs_stage"), "project_stage_runs", ["stage"], unique=False)
    op.create_index(op.f("ix_project_stage_runs_status"), "project_stage_runs", ["status"], unique=False)
    op.create_index(op.f("ix_project_stage_runs_task_id"), "project_stage_runs", ["task_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_project_stage_runs_task_id"), table_name="project_stage_runs")
    op.drop_index(op.f("ix_project_stage_runs_status"), table_name="project_stage_runs")
    op.drop_index(op.f("ix_project_stage_runs_stage"), table_name="project_stage_runs")
    op.drop_index(op.f("ix_project_stage_runs_project_id"), table_name="project_stage_runs")
    op.drop_index(op.f("ix_project_stage_runs_action"), table_name="project_stage_runs")
    op.drop_table("project_stage_runs")
