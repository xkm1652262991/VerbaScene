from sqlalchemy.orm import Session

from app.models import GenerationTask
from app.platform.tasks.repository import TaskRepository
from app.platform.tasks.types import TaskStatus
from app.services.workflow_state_service import mark_stage_failed, mark_stage_ready


def reconcile_project_video_batch(db: Session, parent: GenerationTask) -> None:
    previous_status = parent.status
    TaskRepository().reconcile_parent(db, parent)
    if parent.status == previous_status:
        return
    summary = (
        parent.result_payload.get("summary")
        if isinstance(parent.result_payload, dict)
        and isinstance(parent.result_payload.get("summary"), dict)
        else {}
    )
    if parent.status == TaskStatus.SUCCEEDED.value:
        mark_stage_ready(
            db,
            parent.project_id,
            "videos",
            task_id=parent.id,
            summary=f"{summary.get('succeeded', 0)} 个镜头视频已生成",
            metadata={"batch_task_id": parent.id, "summary": summary},
        )
    elif parent.status == TaskStatus.FAILED.value:
        mark_stage_failed(
            db,
            parent.project_id,
            "videos",
            summary="项目视频批次存在失败或取消的子任务",
            task_id=parent.id,
            error_code=parent.error_code or "batch_children_incomplete",
        )
