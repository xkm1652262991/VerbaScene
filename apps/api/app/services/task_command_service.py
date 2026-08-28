from sqlalchemy.orm import Session

from app.models import GenerationTask
from app.platform.tasks.repository import TaskCreateResult, TaskRepository
from app.platform.tasks.types import TaskStatus
from app.production.contracts import (
    MANUALLY_RETRYABLE_VIDEO_TASK_TYPES,
    VIDEO_CANDIDATE_TASK_TYPE,
)
from app.production.shot_direction.contracts import (
    MANUALLY_RETRYABLE_SHOT_DIRECTION_TASK_TYPES,
    SHOT_DIRECTION_TASK_TYPE,
)
from app.scripts.contracts import (
    MANUALLY_RETRYABLE_SCRIPT_TASK_TYPES,
    SCRIPT_GENERATION_TASK_TYPE,
)
from app.services.stage_run_service import finish_latest_stage_run
from app.services.workflow_state_service import mark_stage_running


MANUALLY_RETRYABLE_TASK_TYPES = (
    MANUALLY_RETRYABLE_SCRIPT_TASK_TYPES
    | MANUALLY_RETRYABLE_VIDEO_TASK_TYPES
    | MANUALLY_RETRYABLE_SHOT_DIRECTION_TASK_TYPES
)


def request_task_cancel(db: Session, task: GenerationTask) -> GenerationTask:
    previous_status = task.status
    TaskRepository().request_cancel(db, task)
    if previous_status == TaskStatus.QUEUED.value and task.status == TaskStatus.CANCELLED.value:
        if task.task_type == SCRIPT_GENERATION_TASK_TYPE:
            finish_latest_stage_run(
                db,
                task.project_id,
                "script",
                status="cancelled",
                task_id=task.id,
                error_code="script_task_cancelled",
                error_message="剧本任务已取消",
            )
        elif task.task_type == VIDEO_CANDIDATE_TASK_TYPE and task.parent_task_id is None:
            finish_latest_stage_run(
                db,
                task.project_id,
                "production",
                status="cancelled",
                task_id=task.id,
                error_code="video_task_cancelled",
                error_message="视频任务已取消",
            )
        elif task.task_type == SHOT_DIRECTION_TASK_TYPE:
            finish_latest_stage_run(
                db,
                task.project_id,
                "production",
                status="cancelled",
                task_id=task.id,
                error_code="shot_direction_cancelled",
                error_message="分镜导演任务已取消",
            )
    return task


def retry_task(db: Session, source: GenerationTask) -> TaskCreateResult:
    result = TaskRepository().retry(
        db,
        source,
        allowed_task_types=MANUALLY_RETRYABLE_TASK_TYPES,
    )
    if not result.created:
        return result
    if source.task_type == VIDEO_CANDIDATE_TASK_TYPE:
        if source.parent_task_id:
            mark_stage_running(
                db,
                source.project_id,
                "videos",
                task_id=source.parent_task_id,
            )
        else:
            mark_stage_running(
                db,
                source.project_id,
                "videos",
                task_id=result.task.id,
            )
    return result
