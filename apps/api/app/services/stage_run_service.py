from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import GenerationTask, Project, ProjectStageRun
from app.platform.tasks.repository import TaskRepository
from app.platform.tasks.types import ACTIVE_TASK_STATUSES
from app.services.failure_reason_service import normalize_failure_reason


MANUAL_REGENERATION_STAGES = {"images", "videos"}


def list_project_stage_runs(
    db: Session,
    project_id: str,
    *,
    stage: str | None = None,
    limit: int = 50,
) -> list[ProjectStageRun]:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    statement = select(ProjectStageRun).where(ProjectStageRun.project_id == project_id)
    if stage:
        statement = statement.where(ProjectStageRun.stage == stage)
    return list(
        db.scalars(
            statement.order_by(ProjectStageRun.created_at.desc()).limit(limit),
        ).all()
    )


def list_project_stage_runs_page(
    db: Session,
    project_id: str,
    *,
    stage: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[ProjectStageRun], int]:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    statement = select(ProjectStageRun).where(ProjectStageRun.project_id == project_id)
    count_statement = select(func.count(ProjectStageRun.id)).where(ProjectStageRun.project_id == project_id)
    if stage:
        statement = statement.where(ProjectStageRun.stage == stage)
        count_statement = count_statement.where(ProjectStageRun.stage == stage)
    runs = list(
        db.scalars(
            statement.order_by(ProjectStageRun.created_at.desc()).offset(offset).limit(limit),
        ).all()
    )
    return runs, db.scalar(count_statement) or 0


def start_stage_run(
    db: Session,
    project_id: str,
    stage_name: str,
    *,
    action: str = "generate",
    task_id: str | None = None,
    input_payload: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> ProjectStageRun:
    if task_id:
        existing = db.scalar(
            select(ProjectStageRun)
            .where(ProjectStageRun.project_id == project_id)
            .where(ProjectStageRun.task_id == task_id)
            .where(ProjectStageRun.status == "running")
            .order_by(ProjectStageRun.created_at.desc())
        )
        if existing:
            return existing

    run = ProjectStageRun(
        project_id=project_id,
        stage=stage_name,
        action=action,
        status="running",
        task_id=task_id,
        input_payload=input_payload or {},
        output_payload={},
        started_at=datetime.now(timezone.utc),
        run_metadata=metadata or {},
    )
    db.add(run)
    db.flush()
    return run


def get_active_stage_run(db: Session, project_id: str, stage_name: str) -> ProjectStageRun | None:
    return db.scalar(
        select(ProjectStageRun)
        .where(ProjectStageRun.project_id == project_id)
        .where(ProjectStageRun.stage == stage_name)
        .where(ProjectStageRun.status.in_(["queued", "running"]))
        .order_by(ProjectStageRun.created_at.desc())
    )


def cancel_stage_run(db: Session, run_id: str) -> ProjectStageRun:
    run = db.get(ProjectStageRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stage run not found")
    if run.status not in {"queued", "running"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only queued or running stage runs can be cancelled",
        )

    run.status = "cancelled"
    run.error_code = "stage_run_cancelled"
    run.error_message = "运行已取消"
    run.finished_at = datetime.now(timezone.utc)
    db.add(run)

    if run.task_id:
        task = db.get(GenerationTask, run.task_id)
        if task and task.status in ACTIVE_TASK_STATUSES:
            TaskRepository().request_cancel(db, task)

    db.commit()
    db.refresh(run)
    return run


def finish_latest_stage_run(
    db: Session,
    project_id: str,
    stage_name: str,
    *,
    status: str,
    task_id: str | None = None,
    output_payload: dict[str, Any] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    failure_reason: dict[str, Any] | None = None,
) -> ProjectStageRun | None:
    statement = (
        select(ProjectStageRun)
        .where(ProjectStageRun.project_id == project_id)
        .where(ProjectStageRun.stage == stage_name)
        .where(ProjectStageRun.status == "running")
        .order_by(ProjectStageRun.created_at.desc())
    )
    if task_id:
        statement = statement.where(ProjectStageRun.task_id == task_id)
    run = db.scalar(statement)
    if run is None:
        return None

    run.status = status
    run.output_payload = output_payload or run.output_payload
    run.error_code = error_code
    run.error_message = error_message
    if status == "failed":
        run_metadata = dict(run.run_metadata or {})
        normalized_failure_reason = failure_reason or normalize_failure_reason(
            code=error_code,
            message=error_message,
            raw_response=run.output_payload,
            stage=stage_name,
        )
        if stage_name in MANUAL_REGENERATION_STAGES:
            normalized_failure_reason = {
                **normalized_failure_reason,
                "retry_policy": "manual_regeneration",
            }
            run_metadata["retry_policy"] = "manual_regeneration"
            run_metadata["retry_reason"] = "media_generation_cost_control"
        run_metadata["failure_reason"] = normalized_failure_reason
        run.run_metadata = run_metadata
    run.finished_at = datetime.now(timezone.utc)
    db.add(run)
    db.flush()
    return run
