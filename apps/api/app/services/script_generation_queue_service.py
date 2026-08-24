import logging
from queue import Queue
from threading import Lock, Thread

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import GenerationTask
from app.services.script_service import SCRIPT_GENERATION_TASK_TYPE, execute_script_generation_task
from app.services.stage_run_service import finish_latest_stage_run
from app.services.task_service import mark_task_cancelled, mark_task_failed
from app.services.workflow_state_service import mark_stage_failed


logger = logging.getLogger(__name__)

_pending_task_ids: Queue[str] = Queue()
_scheduled_task_ids: set[str] = set()
_queue_lock = Lock()
_worker_started = False


def start_script_generation_queue() -> None:
    global _worker_started
    with _queue_lock:
        if _worker_started:
            return
        _worker_started = True

    Thread(
        target=_script_generation_worker,
        name="script-generation-1",
        daemon=True,
    ).start()

    with SessionLocal() as db:
        queued_ids = recover_script_generation_tasks(db)
    for task_id in queued_ids:
        schedule_script_generation_task(task_id)


def recover_script_generation_tasks(db: Session) -> list[str]:
    interrupted = list(
        db.scalars(
            select(GenerationTask)
            .where(GenerationTask.task_type == SCRIPT_GENERATION_TASK_TYPE)
            .where(GenerationTask.status == "running")
        ).all()
    )
    for task in interrupted:
        task.status = "queued"
        task.retry_count += 1
        task.progress_label = "服务重启，等待从检查点继续"
        task.error_code = None
        task.error_message = None
        db.add(task)
    if interrupted:
        db.commit()

    return list(
        db.scalars(
            select(GenerationTask.id)
            .where(GenerationTask.task_type == SCRIPT_GENERATION_TASK_TYPE)
            .where(GenerationTask.status == "queued")
            .order_by(GenerationTask.created_at)
        ).all()
    )


def schedule_script_generation_task(task_id: str) -> None:
    with _queue_lock:
        if task_id in _scheduled_task_ids:
            return
        _scheduled_task_ids.add(task_id)
    _pending_task_ids.put(task_id)


def cancel_queued_script_generation_task(db: Session, task_id: str) -> GenerationTask:
    task = db.get(GenerationTask, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    if task.task_type != SCRIPT_GENERATION_TASK_TYPE:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该任务不属于剧本生成队列")
    if task.status == "running":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="运行中的剧本任务不能从等待队列删除")
    if task.status == "queued":
        mark_task_cancelled(db, task, message="已从剧本生成队列删除")
        finish_latest_stage_run(
            db,
            task.project_id,
            "script",
            status="cancelled",
            task_id=task.id,
            error_code="script_queue_cancelled",
            error_message="剧本任务已从队列删除",
        )
        db.commit()
        db.refresh(task)
    return task


def _script_generation_worker() -> None:
    while True:
        task_id = _pending_task_ids.get()
        try:
            with SessionLocal() as db:
                execute_script_generation_task(db, task_id)
        except Exception:
            logger.exception("Script generation queue worker failed for task %s", task_id)
            with SessionLocal() as db:
                task = db.get(GenerationTask, task_id)
                if task is not None and task.status in {"queued", "running"}:
                    mark_task_failed(
                        db,
                        task,
                        code="script_queue_worker_failed",
                        message="剧本队列执行失败，请查看后端日志",
                    )
                    mark_stage_failed(
                        db,
                        task.project_id,
                        "script",
                        summary="剧本队列执行失败，请查看后端日志",
                        error_code="script_queue_worker_failed",
                    )
                    db.commit()
        finally:
            with _queue_lock:
                _scheduled_task_ids.discard(task_id)
            _pending_task_ids.task_done()
