"""add task progress and agent config

Revision ID: 6d2d5df0f7b1
Revises: b6b080fcf436
Create Date: 2026-06-08 18:20:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "6d2d5df0f7b1"
down_revision: Union[str, None] = "b6b080fcf436"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("generation_tasks", sa.Column("provider_task_id", sa.String(length=255), nullable=True))
    op.add_column(
        "generation_tasks",
        sa.Column("result_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column("generation_tasks", sa.Column("progress", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("generation_tasks", sa.Column("progress_label", sa.String(length=160), nullable=True))
    op.create_index(op.f("ix_generation_tasks_provider_task_id"), "generation_tasks", ["provider_task_id"], unique=False)
    op.execute("UPDATE generation_tasks SET progress = 100, progress_label = '已完成' WHERE status = 'succeeded'")
    op.execute("UPDATE generation_tasks SET progress = 100, progress_label = '失败' WHERE status = 'failed'")
    op.execute("UPDATE generation_tasks SET progress = 10, progress_label = '处理中' WHERE status = 'running' AND progress = 0")

    op.create_table(
        "agent_configs",
        sa.Column("agent_type", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("provider", sa.String(length=120), nullable=True),
        sa.Column("model", sa.String(length=160), nullable=True),
        sa.Column("system_prompt", sa.Text(), nullable=True),
        sa.Column("temperature", sa.String(length=32), nullable=True),
        sa.Column("max_tokens", sa.Integer(), nullable=True),
        sa.Column("max_iterations", sa.Integer(), nullable=True),
        sa.Column("settings", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_agent_configs_agent_type"), "agent_configs", ["agent_type"], unique=False)
    op.create_index(op.f("ix_agent_configs_is_active"), "agent_configs", ["is_active"], unique=False)

    op.create_table(
        "prompt_versions",
        sa.Column("agent_type", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=False, server_default="manual"),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_prompt_versions_agent_type"), "prompt_versions", ["agent_type"], unique=False)
    op.create_index(op.f("ix_prompt_versions_is_active"), "prompt_versions", ["is_active"], unique=False)

    op.alter_column("generation_tasks", "result_payload", server_default=None)
    op.alter_column("generation_tasks", "progress", server_default=None)
    op.alter_column("agent_configs", "settings", server_default=None)
    op.alter_column("agent_configs", "is_active", server_default=None)
    op.alter_column("prompt_versions", "metadata", server_default=None)
    op.alter_column("prompt_versions", "is_active", server_default=None)
    op.alter_column("prompt_versions", "source", server_default=None)


def downgrade() -> None:
    op.drop_index(op.f("ix_prompt_versions_is_active"), table_name="prompt_versions")
    op.drop_index(op.f("ix_prompt_versions_agent_type"), table_name="prompt_versions")
    op.drop_table("prompt_versions")

    op.drop_index(op.f("ix_agent_configs_is_active"), table_name="agent_configs")
    op.drop_index(op.f("ix_agent_configs_agent_type"), table_name="agent_configs")
    op.drop_table("agent_configs")

    op.drop_index(op.f("ix_generation_tasks_provider_task_id"), table_name="generation_tasks")
    op.drop_column("generation_tasks", "progress_label")
    op.drop_column("generation_tasks", "progress")
    op.drop_column("generation_tasks", "result_payload")
    op.drop_column("generation_tasks", "provider_task_id")
