from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models import GenerationTask
from app.platform.tasks.lease import (
    TaskLeaseLostError,
    acquire_task_lease_fence,
    current_task_execution_lease,
)
from app.platform.tasks.types import TERMINAL_TASK_STATUSES, TaskStatus
from app.services.failure_reason_service import attach_failure_reason, normalize_failure_reason


def get_generation_task(db: Session, task_id: str) -> GenerationTask:
    task = db.get(GenerationTask, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return task


def list_generation_tasks(
    db: Session,
    *,
    project_id: str | None = None,
    status_filter: str | None = None,
    parent_task_id: str | None = None,
    task_type: str | None = None,
    resource_key: str | None = None,
    offset: int = 0,
    limit: int = 100,
) -> tuple[list[GenerationTask], int]:
    filters = []
    if project_id:
        filters.append(GenerationTask.project_id == project_id)
    if status_filter:
        filters.append(GenerationTask.status == status_filter)
    if parent_task_id:
        filters.append(GenerationTask.parent_task_id == parent_task_id)
    if task_type:
        filters.append(GenerationTask.task_type == task_type)
    if resource_key:
        filters.append(GenerationTask.resource_key == resource_key)
    statement = select(GenerationTask).where(*filters).order_by(GenerationTask.created_at.desc()).offset(offset).limit(limit)
    total = db.scalar(select(func.count(GenerationTask.id)).where(*filters)) or 0
    return list(db.scalars(statement).all()), int(total)


def list_generation_task_progress(
    db: Session,
    *,
    project_id: str | None = None,
    status_filter: str | None = None,
    offset: int = 0,
    limit: int = 100,
) -> tuple[list[dict[str, Any]], int]:
    """Return the small observable slice needed by runtime progress UIs."""
    filters = []
    if project_id:
        filters.append(GenerationTask.project_id == project_id)
    if status_filter:
        filters.append(GenerationTask.status == status_filter)
    statement = (
        select(
            GenerationTask.id,
            GenerationTask.project_id,
            GenerationTask.task_type,
            GenerationTask.provider,
            GenerationTask.model,
            GenerationTask.status,
            GenerationTask.progress,
            GenerationTask.progress_label,
            GenerationTask.started_at,
            GenerationTask.finished_at,
            GenerationTask.created_at,
            GenerationTask.updated_at,
        )
        .where(*filters)
        .order_by(GenerationTask.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    total = db.scalar(select(func.count(GenerationTask.id)).where(*filters)) or 0
    return [dict(row) for row in db.execute(statement).mappings().all()], int(total)


def mark_task_running(db: Session, task: GenerationTask, label: str, progress: int = 10) -> None:
    task.status = "running"
    task.progress = progress
    task.progress_label = label
    task.started_at = task.started_at or datetime.now(timezone.utc)
    _publish_task_progress(db, task)


def update_task_progress(db: Session, task: GenerationTask, label: str, progress: int) -> None:
    task.progress = max(0, min(99, progress))
    task.progress_label = label
    _publish_task_progress(db, task)


def _publish_task_progress(db: Session, task: GenerationTask) -> None:
    """Commit observable task progress for polling/SSE readers.

    Generation endpoints are synchronous today. Without this commit, their
    progress rows stay inside the request transaction and the UI can only see
    them after the complete model call returns.
    """
    db.add(task)
    db.commit()
    db.refresh(task)


class TaskCompletionConflictError(RuntimeError):
    """A terminal task cannot be completed with a different outcome."""


def begin_task_completion(db: Session, task: GenerationTask) -> bool:
    """Serialize completion delivery and report whether work still needs persisting.

    The task row is the idempotency boundary. Concurrent or replayed completion
    deliveries wait for the first transaction, refresh its durable status, and
    reuse the already-persisted result instead of creating another version.
    """
    execution_lease = current_task_execution_lease()
    if execution_lease is not None and execution_lease.task_id == task.id:
        fenced = acquire_task_lease_fence(
            db,
            task_id=task.id,
            owner=execution_lease.owner,
            token=execution_lease.token,
        )
        if not fenced:
            raise TaskLeaseLostError(
                f"Task {task.id} rejected completion from a superseded lease"
            )
    elif task.lease_owner and task.lease_token:
        fenced = acquire_task_lease_fence(
            db,
            task_id=task.id,
            owner=task.lease_owner,
            token=task.lease_token,
        )
        if not fenced:
            raise TaskLeaseLostError(
                f"Task {task.id} rejected completion from a superseded lease"
            )
    else:
        # A no-op UPDATE is portable across SQLite and PostgreSQL and takes the
        # row lock needed to serialize two completion deliveries.
        result = db.execute(
            update(GenerationTask)
            .where(GenerationTask.id == task.id)
            .values(heartbeat_at=GenerationTask.heartbeat_at)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise TaskCompletionConflictError(f"Task {task.id} no longer exists")

    db.refresh(task)
    if task.status == TaskStatus.SUCCEEDED.value:
        return False
    if task.status in TERMINAL_TASK_STATUSES:
        raise TaskCompletionConflictError(
            f"Task {task.id} is already terminal with status {task.status}"
        )
    return True


def mark_task_succeeded(
    db: Session,
    task: GenerationTask,
    *,
    output_asset_ids: list[str] | None = None,
    result_payload: dict[str, Any] | None = None,
    raw_response: dict[str, Any] | None = None,
    provider_task_id: str | None = None,
) -> None:
    if task.status == TaskStatus.SUCCEEDED.value:
        return
    if task.status in TERMINAL_TASK_STATUSES:
        raise TaskCompletionConflictError(
            f"Task {task.id} cannot transition from {task.status} to succeeded"
        )
    task.status = "succeeded"
    task.progress = 100
    task.progress_label = "已完成"
    task.output_asset_ids = output_asset_ids if output_asset_ids is not None else task.output_asset_ids
    task.result_payload = result_payload if result_payload is not None else task.result_payload
    task.raw_response = raw_response if raw_response is not None else task.raw_response
    task.provider_task_id = provider_task_id if provider_task_id is not None else task.provider_task_id
    task.finished_at = datetime.now(timezone.utc)
    task.active_dedupe_key = None
    task.lease_owner = None
    task.lease_token = None
    task.lease_expires_at = None
    db.flush()


def mark_task_failed(
    db: Session,
    task: GenerationTask,
    *,
    message: str,
    code: str = "task_failed",
    raw_response: dict[str, Any] | None = None,
    provider_task_id: str | None = None,
    failure_reason: dict[str, Any] | None = None,
) -> None:
    if task.status in TERMINAL_TASK_STATUSES:
        return
    base_raw_response = raw_response if raw_response is not None else task.raw_response
    normalized_failure_reason = failure_reason or normalize_failure_reason(
        code=code,
        message=message,
        raw_response=base_raw_response,
        task_type=task.task_type,
        provider=task.provider,
    )
    task.status = "failed"
    task.progress = task.progress or 100
    task.progress_label = "失败"
    task.error_code = code
    task.error_message = message
    task.raw_response = attach_failure_reason(base_raw_response, normalized_failure_reason)
    task.provider_task_id = provider_task_id if provider_task_id is not None else task.provider_task_id
    task.finished_at = datetime.now(timezone.utc)
    task.active_dedupe_key = None
    task.lease_owner = None
    task.lease_token = None
    task.lease_expires_at = None
    db.flush()


def mark_task_cancelled(db: Session, task: GenerationTask, *, message: str = "任务已取消") -> None:
    if task.status in TERMINAL_TASK_STATUSES:
        return
    task.status = "cancelled"
    task.progress_label = message
    task.finished_at = datetime.now(timezone.utc)
    task.active_dedupe_key = None
    task.lease_owner = None
    task.lease_token = None
    task.lease_expires_at = None
    db.flush()
