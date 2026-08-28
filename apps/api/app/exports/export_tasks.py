"""Creation use case for durable project export tasks."""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.exports.contracts import PROJECT_EXPORT_TASK_TYPE
from app.models import GenerationTask
from app.platform.tasks.repository import TaskConflictError, TaskRepository
from app.services.export_service import compile_export_task_input
from app.services.workflow_state_service import mark_stage_running


def create_project_export_task(
    db: Session,
    project_id: str,
    *,
    subtitle_mode: str = "none",
    idempotency_key: str | None = None,
) -> GenerationTask:
    normalized_idempotency_key = (idempotency_key or "").strip()
    if normalized_idempotency_key:
        existing = db.scalar(
            select(GenerationTask)
            .where(GenerationTask.project_id == project_id)
            .where(GenerationTask.task_type == PROJECT_EXPORT_TASK_TYPE)
            .where(GenerationTask.idempotency_key == normalized_idempotency_key)
        )
        if existing is not None:
            return existing
    snapshot = compile_export_task_input(
        db,
        project_id,
        subtitle_mode=subtitle_mode,
    )
    try:
        creation = TaskRepository().create(
            db,
            project_id=project_id,
            task_type=PROJECT_EXPORT_TASK_TYPE,
            resource_key=f"project:{project_id}:export",
            idempotency_key=normalized_idempotency_key or None,
            provider="local",
            model="ffmpeg",
            input_payload=snapshot,
            progress_label="等待成片导出",
            max_retries=0,
        )
    except TaskConflictError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "export_in_progress",
                "message": "该项目已有活动导出任务",
                "task_id": exc.task_id,
            },
        ) from exc
    if creation.created:
        mark_stage_running(db, project_id, "export", task_id=creation.task.id)
    db.commit()
    db.refresh(creation.task)
    return creation.task
