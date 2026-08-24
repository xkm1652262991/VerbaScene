"""add project stages"""

from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "a6b7c8d9e0f1"
down_revision: str | None = "f5a6b7c8d9e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


STAGES = (
    ("input", 10),
    ("script", 20),
    ("entities", 30),
    ("shots", 40),
    ("images", 50),
    ("videos", 60),
    ("audio", 70),
    ("export", 80),
)


def upgrade() -> None:
    op.create_table(
        "project_stages",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("stage", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_task_id", sa.String(length=36), nullable=True),
        sa.Column("stage_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "stage", name="uq_project_stages_project_stage"),
    )
    op.create_index(op.f("ix_project_stages_last_task_id"), "project_stages", ["last_task_id"], unique=False)
    op.create_index(op.f("ix_project_stages_project_id"), "project_stages", ["project_id"], unique=False)
    op.create_index(op.f("ix_project_stages_stage"), "project_stages", ["stage"], unique=False)
    op.create_index(op.f("ix_project_stages_status"), "project_stages", ["status"], unique=False)

    connection = op.get_bind()
    project_ids = [row[0] for row in connection.execute(sa.text("SELECT id FROM projects")).all()]
    for project_id in project_ids:
        for stage, order_index in STAGES:
            status = "pending"
            if stage == "input":
                has_chapter = connection.execute(
                    sa.text("SELECT 1 FROM chapters WHERE project_id = :project_id LIMIT 1"),
                    {"project_id": project_id},
                ).first()
                status = "approved" if has_chapter else "pending"
            connection.execute(
                sa.text(
                    """
                    INSERT INTO project_stages (
                        id, project_id, stage, status, order_index, stage_metadata, created_at, updated_at
                    )
                    VALUES (
                        :id, :project_id, :stage, :status, :order_index,
                        '{}'::jsonb, now(), now()
                    )
                    """
                ),
                {
                    "id": str(uuid4()),
                    "project_id": project_id,
                    "stage": stage,
                    "status": status,
                    "order_index": order_index,
                },
            )


def downgrade() -> None:
    op.drop_index(op.f("ix_project_stages_status"), table_name="project_stages")
    op.drop_index(op.f("ix_project_stages_stage"), table_name="project_stages")
    op.drop_index(op.f("ix_project_stages_project_id"), table_name="project_stages")
    op.drop_index(op.f("ix_project_stages_last_task_id"), table_name="project_stages")
    op.drop_table("project_stages")
