import logging
from queue import Queue
from threading import Lock, Thread

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import SessionLocal
from app.models import GenerationTask
from app.services.video_generation_service import execute_single_shot_video_candidate_task
from app.services.task_service import mark_task_cancelled, mark_task_failed


logger = logging.getLogger(__name__)

VIDEO_QUEUE_TASK_TYPE = "single_shot_video_candidate_generation"
_pending_task_ids: Queue[str] = Queue()
_scheduled_task_ids: set[str] = set()
_queue_lock = Lock()
_workers_started = False


def start_video_generation_queue() -> None:
    global _workers_started
    with _queue_lock:
        if _workers_started:
            return
        _workers_started = True

    worker_count = max(1, min(settings.video_generation_concurrency, 2))
    for index in range(worker_count):
        Thread(
            target=_video_generation_worker,
            name=f"video-generation-{index + 1}",
            daemon=True,
        ).start()

    with SessionLocal() as db:
        interrupted = list(
            db.scalars(
                select(GenerationTask)
                .where(GenerationTask.task_type == VIDEO_QUEUE_TASK_TYPE)
                .where(GenerationTask.status == "running")
            ).all()
        )
        for task in interrupted:
            mark_task_failed(
                db,
                task,
                code="video_queue_interrupted",
                message="后端服务曾重启，请重新加入视频生成队列",
            )
        if interrupted:
            db.commit()

        queued_ids = list(
            db.scalars(
                select(GenerationTask.id)
                .where(GenerationTask.task_type == VIDEO_QUEUE_TASK_TYPE)
                .where(GenerationTask.status == "queued")
                .order_by(GenerationTask.created_at)
            ).all()
        )
    for task_id in queued_ids:
        schedule_video_generation_task(task_id)


def schedule_video_generation_task(task_id: str) -> None:
    with _queue_lock:
        if task_id in _scheduled_task_ids:
            return
        _scheduled_task_ids.add(task_id)
    _pending_task_ids.put(task_id)


def cancel_queued_video_generation_task(db: Session, task_id: str) -> GenerationTask:
    task = db.get(GenerationTask, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    if task.task_type != VIDEO_QUEUE_TASK_TYPE:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该任务不属于视频生成队列")
    if task.status == "running":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="运行中的视频任务不能从等待队列删除")
    if task.status == "queued":
        mark_task_cancelled(db, task, message="已从视频生成队列删除")
        db.commit()
        db.refresh(task)
    return task


def _video_generation_worker() -> None:
    while True:
        task_id = _pending_task_ids.get()
        try:
            with SessionLocal() as db:
                execute_single_shot_video_candidate_task(db, task_id)
        except Exception:
            logger.exception("Video generation queue worker failed for task %s", task_id)
            with SessionLocal() as db:
                task = db.get(GenerationTask, task_id)
                if task is not None and task.status in {"queued", "running"}:
                    mark_task_failed(
                        db,
                        task,
                        code="video_queue_worker_failed",
                        message="视频队列执行失败，请查看后端日志",
                    )
                    db.commit()
        finally:
            with _queue_lock:
                _scheduled_task_ids.discard(task_id)
            _pending_task_ids.task_done()
