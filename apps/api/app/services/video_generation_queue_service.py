"""Compatibility facade for the retired video-only in-memory queue."""

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models import GenerationTask
from app.platform.tasks.defaults import configure_default_task_runtime
from app.platform.tasks.repository import TaskRepository
from app.production.contracts import VIDEO_CANDIDATE_TASK_TYPE


VIDEO_QUEUE_TASK_TYPE = VIDEO_CANDIDATE_TASK_TYPE


def start_video_generation_queue() -> None:
    configure_default_task_runtime().start()


def schedule_video_generation_task(_task_id: str) -> None:
    configure_default_task_runtime().wake()


def cancel_queued_video_generation_task(db: Session, task_id: str) -> GenerationTask:
    task = db.get(GenerationTask, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    if task.task_type not in {
        VIDEO_CANDIDATE_TASK_TYPE,
        "single_shot_video_candidate_generation",
    }:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该任务不属于视频生成队列")
    TaskRepository().request_cancel(db, task)
    db.commit()
    db.refresh(task)
    configure_default_task_runtime().wake()
    return task
