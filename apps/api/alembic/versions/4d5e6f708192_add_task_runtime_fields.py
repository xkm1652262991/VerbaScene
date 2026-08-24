"""add unified task runtime fields

Revision ID: 4d5e6f708192
Revises: 3c4d5e6f7081
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "4d5e6f708192"
down_revision: str | None = "3c4d5e6f7081"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("generation_tasks", sa.Column("parent_task_id", sa.String(length=36), nullable=True))
    op.add_column("generation_tasks", sa.Column("retry_of_task_id", sa.String(length=36), nullable=True))
    op.add_column("generation_tasks", sa.Column("resource_key", sa.String(length=255), nullable=True))
    op.add_column("generation_tasks", sa.Column("active_dedupe_key", sa.String(length=255), nullable=True))
    op.add_column("generation_tasks", sa.Column("idempotency_key", sa.String(length=255), nullable=True))
    op.add_column(
        "generation_tasks",
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column("generation_tasks", sa.Column("available_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("generation_tasks", sa.Column("lease_owner", sa.String(length=160), nullable=True))
    op.add_column("generation_tasks", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("generation_tasks", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("generation_tasks", sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True))

    # The legacy video worker never persisted a remote task id before its
    # blocking provider call. A row left running at migration time therefore
    # has an unknowable submission outcome and must never be submitted again.
    op.execute(
        "UPDATE generation_tasks SET status = 'failed', "
        "error_code = 'provider_submission_uncertain', "
        "error_message = 'Legacy video task was interrupted during provider submission', "
        "progress_label = 'Migration stopped an uncertain legacy submission', "
        "finished_at = COALESCE(finished_at, CURRENT_TIMESTAMP), "
        "active_dedupe_key = NULL, lease_owner = NULL, lease_expires_at = NULL "
        "WHERE task_type IN ('single_shot_video_candidate_generation', "
        "'shot_video_regeneration_candidate') AND status = 'running'"
    )
    op.execute(
        "UPDATE generation_tasks SET task_type = 'shot_video_candidate_generation' "
        "WHERE task_type IN ('single_shot_video_candidate_generation', "
        "'shot_video_regeneration_candidate')"
    )
    op.execute(
        "UPDATE generation_tasks SET resource_key = 'project' || CHR(58) || "
        "project_id || CHR(58) || 'script' "
        "WHERE task_type = 'script_generation' AND resource_key IS NULL"
    )
    op.execute(
        "UPDATE generation_tasks SET resource_key = 'shot' || CHR(58) || "
        "(input_payload ->> 'shot_id') || CHR(58) || 'video' "
        "WHERE task_type = 'shot_video_candidate_generation' "
        "AND resource_key IS NULL AND input_payload ->> 'shot_id' IS NOT NULL"
    )
    op.execute(
        "WITH ranked AS ("
        "SELECT id, ROW_NUMBER() OVER ("
        "PARTITION BY task_type, resource_key ORDER BY created_at, id"
        ") AS position FROM generation_tasks "
        "WHERE resource_key IS NOT NULL AND status IN ("
        "'queued', 'running', 'waiting_provider', 'waiting_children', 'cancelling'"
        ")) UPDATE generation_tasks AS duplicate SET status = 'failed', "
        "error_code = 'task_dedupe_migration_conflict', "
        "error_message = 'A duplicate active task was superseded during migration', "
        "progress_label = 'Migration resolved duplicate active task', "
        "finished_at = COALESCE(finished_at, CURRENT_TIMESTAMP) "
        "FROM ranked WHERE duplicate.id = ranked.id AND ranked.position > 1"
    )
    op.execute(
        "UPDATE generation_tasks SET active_dedupe_key = task_type || CHR(58) || resource_key "
        "WHERE resource_key IS NOT NULL AND active_dedupe_key IS NULL "
        "AND status IN ('queued', 'running', 'waiting_provider', "
        "'waiting_children', 'cancelling')"
    )
    op.execute(
        "UPDATE generation_tasks SET available_at = COALESCE(available_at, created_at) "
        "WHERE status IN ('queued', 'waiting_provider')"
    )
    op.execute(
        "UPDATE generation_tasks SET lease_expires_at = TIMESTAMPTZ '1970-01-01 00:00:00+00' "
        "WHERE status IN ('running', 'cancelling') AND lease_expires_at IS NULL "
        "AND task_type IN ('script_generation', 'shot_video_candidate_generation')"
    )

    op.create_foreign_key(
        "fk_generation_tasks_parent_task_id",
        "generation_tasks",
        "generation_tasks",
        ["parent_task_id"],
        ["id"],
        ondelete="SET NULL",
    )
    for column in (
        "parent_task_id",
        "retry_of_task_id",
        "resource_key",
        "active_dedupe_key",
        "idempotency_key",
        "available_at",
        "lease_owner",
        "lease_expires_at",
    ):
        op.create_index(f"ix_generation_tasks_{column}", "generation_tasks", [column])
    op.create_unique_constraint(
        "uq_generation_tasks_active_dedupe_key",
        "generation_tasks",
        ["active_dedupe_key"],
    )
    op.create_unique_constraint(
        "uq_generation_tasks_idempotency_scope",
        "generation_tasks",
        ["project_id", "task_type", "idempotency_key"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_generation_tasks_idempotency_scope", "generation_tasks", type_="unique")
    op.drop_constraint("uq_generation_tasks_active_dedupe_key", "generation_tasks", type_="unique")
    for column in reversed(
        (
            "parent_task_id",
            "retry_of_task_id",
            "resource_key",
            "active_dedupe_key",
            "idempotency_key",
            "available_at",
            "lease_owner",
            "lease_expires_at",
        )
    ):
        op.drop_index(f"ix_generation_tasks_{column}", table_name="generation_tasks")
    op.drop_constraint("fk_generation_tasks_parent_task_id", "generation_tasks", type_="foreignkey")
    for column in (
        "cancel_requested_at",
        "heartbeat_at",
        "lease_expires_at",
        "lease_owner",
        "available_at",
        "max_retries",
        "idempotency_key",
        "active_dedupe_key",
        "resource_key",
        "retry_of_task_id",
        "parent_task_id",
    ):
        op.drop_column("generation_tasks", column)
