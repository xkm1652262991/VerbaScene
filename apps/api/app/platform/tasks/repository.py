from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from uuid import uuid4

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.models import GenerationTask
from app.platform.tasks.lease import (
    acquire_task_lease_fence,
    current_task_execution_lease,
)
from app.platform.tasks.types import ACTIVE_TASK_STATUSES, TERMINAL_TASK_STATUSES, TaskStatus


class TaskConflictError(RuntimeError):
    def __init__(self, message: str, *, task_id: str | None = None) -> None:
        super().__init__(message)
        self.task_id = task_id


@dataclass(frozen=True)
class TaskCreateResult:
    task: GenerationTask
    created: bool


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def active_dedupe_key(task_type: str, resource_key: str) -> str:
    value = f"{task_type}:{resource_key}"
    if len(value) > 255:
        raise ValueError("Task dedupe key exceeds 255 characters")
    return value


class TaskRepository:
    """Persistence operations for GenerationTask.

    Methods only query and flush. The API use case or worker owns commit and
    rollback, which keeps transaction boundaries visible to callers.
    """

    def create(
        self,
        db: Session,
        *,
        project_id: str,
        task_type: str,
        input_payload: dict[str, Any],
        provider: str | None = None,
        model: str | None = None,
        parent_task_id: str | None = None,
        retry_of_task_id: str | None = None,
        resource_key: str | None = None,
        idempotency_key: str | None = None,
        max_retries: int = 1,
        status: TaskStatus | str = TaskStatus.QUEUED,
        progress: int = 0,
        progress_label: str | None = None,
        raw_response: dict[str, Any] | None = None,
        result_payload: dict[str, Any] | None = None,
        dedupe: bool = True,
    ) -> TaskCreateResult:
        normalized_idempotency_key = (idempotency_key or "").strip() or None
        if normalized_idempotency_key:
            existing = db.scalar(
                select(GenerationTask)
                .where(GenerationTask.project_id == project_id)
                .where(GenerationTask.task_type == task_type)
                .where(GenerationTask.idempotency_key == normalized_idempotency_key)
            )
            if existing is not None:
                return TaskCreateResult(existing, False)

        dedupe_key = (
            active_dedupe_key(task_type, resource_key)
            if dedupe and resource_key
            else None
        )
        task = GenerationTask(
            project_id=project_id,
            task_type=task_type,
            parent_task_id=parent_task_id,
            retry_of_task_id=retry_of_task_id,
            resource_key=resource_key,
            active_dedupe_key=dedupe_key,
            idempotency_key=normalized_idempotency_key,
            provider=provider,
            model=model,
            input_payload=input_payload,
            status=str(status),
            progress=progress,
            progress_label=progress_label,
            max_retries=max(0, max_retries),
            available_at=utc_now(),
            raw_response=raw_response or {},
            result_payload=result_payload or {},
        )
        try:
            with db.begin_nested():
                db.add(task)
                db.flush()
        except IntegrityError as exc:
            if normalized_idempotency_key:
                existing = db.scalar(
                    select(GenerationTask)
                    .where(GenerationTask.project_id == project_id)
                    .where(GenerationTask.task_type == task_type)
                    .where(GenerationTask.idempotency_key == normalized_idempotency_key)
                )
                if existing is not None:
                    return TaskCreateResult(existing, False)
            existing = (
                db.scalar(
                    select(GenerationTask).where(
                        GenerationTask.active_dedupe_key == dedupe_key
                    )
                )
                if dedupe_key
                else None
            )
            raise TaskConflictError(
                "An active task already owns this resource",
                task_id=existing.id if existing else None,
            ) from exc
        return TaskCreateResult(task, True)

    def get(self, db: Session, task_id: str, *, with_children: bool = False) -> GenerationTask | None:
        statement = select(GenerationTask).where(GenerationTask.id == task_id)
        if with_children:
            statement = statement.options(selectinload(GenerationTask.children))
        return db.scalar(statement)

    def claim_next(
        self,
        db: Session,
        *,
        task_types: Iterable[str],
        owner: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> GenerationTask | None:
        claimed_at = now or utc_now()
        task_type_values = tuple(task_types)
        if not task_type_values:
            return None
        due = or_(
            GenerationTask.available_at.is_(None),
            GenerationTask.available_at <= claimed_at,
        )
        lease_free = or_(
            GenerationTask.lease_owner.is_(None),
            GenerationTask.lease_expires_at.is_(None),
            GenerationTask.lease_expires_at <= claimed_at,
        )
        eligible = or_(
            and_(GenerationTask.status == TaskStatus.QUEUED.value, due, lease_free),
            and_(GenerationTask.status == TaskStatus.WAITING_PROVIDER.value, due, lease_free),
            and_(GenerationTask.status == TaskStatus.CANCELLING.value, lease_free),
            and_(
                GenerationTask.status == TaskStatus.RUNNING.value,
                GenerationTask.lease_expires_at.is_not(None),
                GenerationTask.lease_expires_at <= claimed_at,
            ),
        )
        candidate_id = db.scalar(
            select(GenerationTask.id)
            .where(GenerationTask.task_type.in_(task_type_values))
            .where(eligible)
            .order_by(GenerationTask.available_at, GenerationTask.created_at)
            .limit(1)
        )
        if candidate_id is None:
            return None

        lease_expires_at = claimed_at + timedelta(seconds=max(1, lease_seconds))
        lease_token = str(uuid4())
        result = db.execute(
            update(GenerationTask)
            .where(GenerationTask.id == candidate_id)
            .where(eligible)
            .values(
                status=case(
                    (GenerationTask.status == TaskStatus.QUEUED.value, TaskStatus.RUNNING.value),
                    else_=GenerationTask.status,
                ),
                started_at=func.coalesce(GenerationTask.started_at, claimed_at),
                heartbeat_at=claimed_at,
                lease_owner=owner,
                lease_token=lease_token,
                lease_expires_at=lease_expires_at,
            )
        )
        if result.rowcount != 1:
            return None
        db.flush()
        return db.get(GenerationTask, candidate_id)

    def heartbeat(
        self,
        db: Session,
        *,
        task_id: str,
        owner: str,
        lease_token: str | None = None,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> bool:
        heartbeat_at = now or utc_now()
        result = db.execute(
            update(GenerationTask)
            .where(GenerationTask.id == task_id)
            .where(GenerationTask.lease_owner == owner)
            .where(
                GenerationTask.lease_token == lease_token
                if lease_token is not None
                else GenerationTask.lease_token.is_not(None)
            )
            .where(GenerationTask.status.in_(tuple(ACTIVE_TASK_STATUSES)))
            .values(
                heartbeat_at=heartbeat_at,
                lease_expires_at=heartbeat_at + timedelta(seconds=max(1, lease_seconds)),
            )
        )
        db.flush()
        return result.rowcount == 1

    def release_lease(
        self,
        db: Session,
        *,
        task_id: str,
        owner: str,
        lease_token: str | None = None,
        available_at: datetime | None = None,
    ) -> bool:
        values: dict[str, Any] = {
            "lease_owner": None,
            "lease_token": None,
            "lease_expires_at": None,
            "heartbeat_at": utc_now(),
        }
        if available_at is not None:
            values["available_at"] = available_at
        result = db.execute(
            update(GenerationTask)
            .where(GenerationTask.id == task_id)
            .where(GenerationTask.lease_owner == owner)
            .where(
                GenerationTask.lease_token == lease_token
                if lease_token is not None
                else GenerationTask.lease_token.is_not(None)
            )
            .values(**values)
        )
        db.flush()
        return result.rowcount == 1

    def finish(
        self,
        db: Session,
        task: GenerationTask,
        *,
        status: TaskStatus,
        progress_label: str,
        result_payload: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        raw_response: dict[str, Any] | None = None,
        owner: str | None = None,
        lease_token: str | None = None,
    ) -> bool:
        if status.value not in TERMINAL_TASK_STATUSES:
            raise ValueError("finish() requires a terminal status")
        if task.status in TERMINAL_TASK_STATUSES:
            return task.status == status.value

        execution_lease = current_task_execution_lease()
        expected_owner: str | None
        expected_token: str | None
        if execution_lease is not None and execution_lease.task_id == task.id:
            expected_owner = execution_lease.owner
            expected_token = execution_lease.token
        else:
            expected_owner = owner or task.lease_owner
            expected_token = lease_token or task.lease_token
        if expected_owner and expected_token and not acquire_task_lease_fence(
            db,
            task_id=task.id,
            owner=expected_owner,
            token=expected_token,
        ):
            db.expire(task)
            return False
        task.status = status.value
        task.progress = 100 if status == TaskStatus.SUCCEEDED else task.progress
        task.progress_label = progress_label
        task.result_payload = result_payload if result_payload is not None else task.result_payload
        task.error_code = error_code
        task.error_message = error_message
        task.raw_response = raw_response if raw_response is not None else task.raw_response
        task.finished_at = utc_now()
        task.active_dedupe_key = None
        task.lease_owner = None
        task.lease_token = None
        task.lease_expires_at = None
        db.add(task)
        db.flush()
        return True

    def request_cancel(self, db: Session, task: GenerationTask) -> GenerationTask:
        if task.status in TERMINAL_TASK_STATUSES:
            return task
        now = utc_now()
        task.cancel_requested_at = task.cancel_requested_at or now
        if task.status == TaskStatus.QUEUED.value:
            self.finish(
                db,
                task,
                status=TaskStatus.CANCELLED,
                progress_label="任务已取消",
            )
        else:
            task.status = TaskStatus.CANCELLING.value
            task.progress_label = "正在取消"
            db.add(task)
            db.flush()

        children = list(
            db.scalars(
                select(GenerationTask).where(GenerationTask.parent_task_id == task.id)
            ).all()
        )
        if children:
            for child in children:
                if child.status in TERMINAL_TASK_STATUSES:
                    continue
                child.cancel_requested_at = child.cancel_requested_at or now
                if child.status == TaskStatus.QUEUED.value:
                    self.finish(
                        db,
                        child,
                        status=TaskStatus.CANCELLED,
                        progress_label="父任务已取消",
                    )
                else:
                    child.status = TaskStatus.CANCELLING.value
                    child.progress_label = "父任务请求取消"
                    db.add(child)
            db.flush()
        return task

    def retry(
        self,
        db: Session,
        source: GenerationTask,
        *,
        allowed_task_types: Iterable[str],
    ) -> TaskCreateResult:
        allowed = frozenset(allowed_task_types)
        if source.task_type not in allowed:
            raise TaskConflictError(
                f"Task type does not support manual retry: {source.task_type}",
                task_id=source.id,
            )
        if source.status not in {TaskStatus.FAILED.value, TaskStatus.CANCELLED.value}:
            raise TaskConflictError("Only failed or cancelled tasks can be retried", task_id=source.id)
        if source.children:
            raise TaskConflictError(
                "Retry failed or cancelled child tasks individually",
                task_id=source.id,
            )
        raw_response = (
            _checkpoint_manual_retry_response(source)
            if source.task_type in {"script_generation", "shot_breakdown"}
            else {}
        )
        result = self.create(
            db,
            project_id=source.project_id,
            task_type=source.task_type,
            parent_task_id=source.parent_task_id,
            retry_of_task_id=source.id,
            resource_key=source.resource_key,
            input_payload=dict(source.input_payload or {}),
            provider=source.provider,
            model=source.model,
            max_retries=source.max_retries,
            progress_label="等待人工重试",
            raw_response=dict(raw_response or {}),
        )
        if result.created and source.parent_task_id:
            parent = db.get(GenerationTask, source.parent_task_id)
            if parent is not None:
                parent_dedupe_key = active_dedupe_key(
                    parent.task_type,
                    parent.resource_key or f"project:{parent.project_id}:videos",
                )
                conflicting_parent = db.scalar(
                    select(GenerationTask)
                    .where(GenerationTask.active_dedupe_key == parent_dedupe_key)
                    .where(GenerationTask.id != parent.id)
                )
                if conflicting_parent is not None:
                    raise TaskConflictError(
                        "Another parent batch is active",
                        task_id=conflicting_parent.id,
                    )
                parent.status = TaskStatus.WAITING_CHILDREN.value
                parent.finished_at = None
                parent.cancel_requested_at = None
                parent.error_code = None
                parent.error_message = None
                parent.progress_label = "子任务已人工重试"
                parent.active_dedupe_key = parent_dedupe_key
                db.add(parent)
                db.flush()
        return result

    def reconcile_parent(self, db: Session, parent: GenerationTask) -> bool:
        all_children = list(
            db.scalars(
                select(GenerationTask).where(GenerationTask.parent_task_id == parent.id)
            ).all()
        )
        retried_task_ids = {
            child.retry_of_task_id
            for child in all_children
            if child.retry_of_task_id
        }
        children = [child for child in all_children if child.id not in retried_task_ids]
        if not children:
            self.finish(
                db,
                parent,
                status=TaskStatus.FAILED,
                progress_label="批次没有子任务",
                error_code="batch_children_missing",
                error_message="Project video batch has no child tasks",
                result_payload={"summary": {"total": 0}},
            )
            return True
        counts = {status: 0 for status in (*ACTIVE_TASK_STATUSES, *TERMINAL_TASK_STATUSES)}
        for child in children:
            counts[child.status] = counts.get(child.status, 0) + 1
        terminal_count = sum(counts.get(value, 0) for value in TERMINAL_TASK_STATUSES)
        candidate_ids = [
            str(candidate_id)
            for child in children
            for candidate_id in (
                child.result_payload.get("candidate_ids", [])
                if isinstance(child.result_payload, dict)
                and isinstance(child.result_payload.get("candidate_ids"), list)
                else []
            )
        ]
        output_asset_ids = [
            str(asset_id)
            for child in children
            for asset_id in (child.output_asset_ids or [])
        ]
        parent.progress = int((terminal_count / len(children)) * 100)
        parent.result_payload = {
            **dict(parent.result_payload or {}),
            "candidate_ids": candidate_ids,
            "output_asset_ids": output_asset_ids,
            "failed_child_task_ids": [
                child.id
                for child in children
                if child.status in {TaskStatus.FAILED.value, TaskStatus.CANCELLED.value}
            ],
            "summary": {
                "total": len(children),
                "attempt_count": len(all_children),
                **{key: counts.get(key, 0) for key in counts},
            },
        }
        if terminal_count != len(children):
            if parent.status != TaskStatus.CANCELLING.value:
                parent.status = TaskStatus.WAITING_CHILDREN.value
                parent.progress_label = f"等待子任务（{terminal_count}/{len(children)}）"
            db.add(parent)
            db.flush()
            return True
        if parent.cancel_requested_at is not None:
            self.finish(
                db,
                parent,
                status=TaskStatus.CANCELLED,
                progress_label="批次已取消",
                result_payload=parent.result_payload,
            )
        elif counts.get(TaskStatus.FAILED.value, 0) or counts.get(TaskStatus.CANCELLED.value, 0):
            self.finish(
                db,
                parent,
                status=TaskStatus.FAILED,
                progress_label="批次已结束，部分子任务未成功",
                error_code="batch_children_incomplete",
                error_message="One or more child tasks failed or were cancelled",
                result_payload=parent.result_payload,
            )
        else:
            self.finish(
                db,
                parent,
                status=TaskStatus.SUCCEEDED,
                progress_label="批次已完成",
                result_payload=parent.result_payload,
            )
        return True


def _checkpoint_manual_retry_response(source: GenerationTask) -> dict[str, Any]:
    raw_response = source.raw_response if isinstance(source.raw_response, dict) else {}
    checkpoint = deepcopy(raw_response.get("checkpoint") or {})
    failure_phase = str(raw_response.get("failure_phase") or "")
    inflight = checkpoint.pop("inflight_phase", None)
    if not failure_phase and isinstance(inflight, dict):
        failure_phase = str(inflight.get("phase") or "")
    responses = dict(checkpoint.get("responses") or {})
    failed_phases = {
        str(phase)
        for phase, response in responses.items()
        if isinstance(response, dict) and response.get("status") != "succeeded"
    }
    if failure_phase:
        failed_phases.add(failure_phase)
    for phase in failed_phases:
        responses.pop(phase, None)
    checkpoint["responses"] = responses
    trace = dict(checkpoint.get("pipeline_trace") or {})
    trace["phases"] = [
        item
        for item in trace.get("phases") or []
        if not isinstance(item, dict) or str(item.get("phase") or "") not in failed_phases
    ]
    checkpoint["pipeline_trace"] = trace
    return {
        "checkpoint": checkpoint,
        "manual_retry_of": source.id,
    }
