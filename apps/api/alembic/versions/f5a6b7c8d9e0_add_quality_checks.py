"""add quality checks"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "f5a6b7c8d9e0"
down_revision: str | None = "e4f5a6b7c8d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "quality_checks",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("stage", sa.String(length=80), nullable=False),
        sa.Column("target_type", sa.String(length=80), nullable=False),
        sa.Column("target_id", sa.String(length=36), nullable=True),
        sa.Column("shot_id", sa.String(length=36), nullable=True),
        sa.Column("asset_id", sa.String(length=36), nullable=True),
        sa.Column("method", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("score", sa.Numeric(5, 2), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("issues", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("suggestions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("raw_response", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_quality_checks_asset_id"), "quality_checks", ["asset_id"], unique=False)
    op.create_index(op.f("ix_quality_checks_method"), "quality_checks", ["method"], unique=False)
    op.create_index(op.f("ix_quality_checks_project_id"), "quality_checks", ["project_id"], unique=False)
    op.create_index(op.f("ix_quality_checks_shot_id"), "quality_checks", ["shot_id"], unique=False)
    op.create_index(op.f("ix_quality_checks_stage"), "quality_checks", ["stage"], unique=False)
    op.create_index(op.f("ix_quality_checks_status"), "quality_checks", ["status"], unique=False)
    op.create_index(op.f("ix_quality_checks_target_id"), "quality_checks", ["target_id"], unique=False)
    op.create_index(op.f("ix_quality_checks_target_type"), "quality_checks", ["target_type"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_quality_checks_target_type"), table_name="quality_checks")
    op.drop_index(op.f("ix_quality_checks_target_id"), table_name="quality_checks")
    op.drop_index(op.f("ix_quality_checks_status"), table_name="quality_checks")
    op.drop_index(op.f("ix_quality_checks_stage"), table_name="quality_checks")
    op.drop_index(op.f("ix_quality_checks_shot_id"), table_name="quality_checks")
    op.drop_index(op.f("ix_quality_checks_project_id"), table_name="quality_checks")
    op.drop_index(op.f("ix_quality_checks_method"), table_name="quality_checks")
    op.drop_index(op.f("ix_quality_checks_asset_id"), table_name="quality_checks")
    op.drop_table("quality_checks")
