"""Aggregate image batch children and close their project stage run."""

from sqlalchemy.orm import Session

from app.models import GenerationTask
from app.platform.tasks.repository import TaskRepository
from app.platform.tasks.types import TaskStatus
from app.services.workflow_state_service import mark_stage_failed, mark_stage_ready
from app.services.stage_run_service import finish_latest_stage_run


def reconcile_image_batch(db: Session, parent: GenerationTask) -> None:
    TaskRepository().reconcile_parent(db, parent)
    if parent.status == TaskStatus.SUCCEEDED.value:
        candidate_ids = (
            parent.result_payload.get("candidate_ids", [])
            if isinstance(parent.result_payload, dict)
            else []
        )
        mark_stage_ready(
            db,
            parent.project_id,
            "images",
            task_id=parent.id,
            summary=f"{len(candidate_ids)} 张图片候选已生成，等待确认入库",
            metadata={
                "candidate_count": len(candidate_ids),
                "kind": parent.task_type,
            },
        )
    elif parent.status == TaskStatus.FAILED.value:
        mark_stage_failed(
            db,
            parent.project_id,
            "images",
            summary=parent.error_message or "图片批次存在失败或取消的子任务",
            task_id=parent.id,
            error_code=parent.error_code or "image_batch_incomplete",
        )
    elif parent.status == TaskStatus.CANCELLED.value:
        finish_latest_stage_run(
            db,
            parent.project_id,
            "assets",
            status="cancelled",
            task_id=parent.id,
            error_code="image_batch_cancelled",
            error_message="图片批次已取消",
        )
