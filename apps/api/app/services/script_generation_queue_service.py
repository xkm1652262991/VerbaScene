"""Compatibility facade for the retired script-only in-memory queue."""

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import GenerationTask
from app.platform.tasks.defaults import configure_default_task_runtime
from app.platform.tasks.repository import TaskRepository
from app.services.script_service import SCRIPT_GENERATION_TASK_TYPE


def start_script_generation_queue() -> None:
    configure_default_task_runtime().start()


def schedule_script_generation_task(_task_id: str) -> None:
    configure_default_task_runtime().wake()


def recover_script_generation_tasks(db: Session) -> list[str]:
    """Report claimable tasks without rewriting unexpired running leases."""
    now = datetime.now(timezone.utc)
    return list(
        db.scalars(
            select(GenerationTask.id)
            .where(GenerationTask.task_type == SCRIPT_GENERATION_TASK_TYPE)
            .where(
                or_(
                    GenerationTask.status == "queued",
                    (
                        (GenerationTask.status == "running")
                        & (GenerationTask.lease_expires_at.is_not(None))
                        & (GenerationTask.lease_expires_at <= now)
                    ),
                )
            )
            .order_by(GenerationTask.created_at)
        ).all()
    )


def cancel_queued_script_generation_task(db: Session, task_id: str) -> GenerationTask:
    task = db.get(GenerationTask, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    if task.task_type != SCRIPT_GENERATION_TASK_TYPE:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该任务不属于剧本生成队列")
    TaskRepository().request_cancel(db, task)
    db.commit()
    db.refresh(task)
    configure_default_task_runtime().wake()
    return task
