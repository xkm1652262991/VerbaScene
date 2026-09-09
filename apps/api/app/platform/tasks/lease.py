from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterator

from sqlalchemy import event, update
from sqlalchemy.orm import Session

from app.models import GenerationTask
from app.platform.tasks.types import ACTIVE_TASK_STATUSES


@dataclass(frozen=True)
class TaskExecutionLease:
    task_id: str
    owner: str
    token: str


class TaskLeaseLostError(RuntimeError):
    """Raised when a worker tries to write after its claim is no longer valid."""


_current_lease: ContextVar[TaskExecutionLease | None] = ContextVar(
    "current_task_execution_lease",
    default=None,
)
_SESSION_FENCE_KEY = "task_execution_lease_fence"


@contextmanager
def task_execution_lease(
    task_id: str,
    owner: str,
    token: str,
) -> Iterator[TaskExecutionLease]:
    lease = TaskExecutionLease(task_id=task_id, owner=owner, token=token)
    context_token = _current_lease.set(lease)
    try:
        yield lease
    finally:
        _current_lease.reset(context_token)


def current_task_execution_lease() -> TaskExecutionLease | None:
    return _current_lease.get()


def acquire_task_lease_fence(
    db: Session,
    *,
    task_id: str,
    owner: str,
    token: str,
    now: datetime | None = None,
) -> bool:
    """Atomically validate and lock one live lease for the current transaction."""
    transaction = db.get_transaction()
    marker = db.info.get(_SESSION_FENCE_KEY)
    if marker == (transaction, task_id, owner, token):
        return True

    checked_at = now or datetime.now(timezone.utc)
    result = db.execute(
        update(GenerationTask)
        .where(GenerationTask.id == task_id)
        .where(GenerationTask.lease_owner == owner)
        .where(GenerationTask.lease_token == token)
        .where(GenerationTask.lease_expires_at.is_not(None))
        .where(GenerationTask.lease_expires_at > checked_at)
        .where(GenerationTask.status.in_(tuple(ACTIVE_TASK_STATUSES)))
        .values(heartbeat_at=GenerationTask.heartbeat_at)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        return False
    db.info[_SESSION_FENCE_KEY] = (
        db.get_transaction(),
        task_id,
        owner,
        token,
    )
    return True


def require_current_task_lease(db: Session) -> None:
    lease = current_task_execution_lease()
    if lease is None:
        return
    if not acquire_task_lease_fence(
        db,
        task_id=lease.task_id,
        owner=lease.owner,
        token=lease.token,
    ):
        raise TaskLeaseLostError(
            f"Task {lease.task_id} rejected a write from an expired or superseded lease"
        )


@event.listens_for(Session, "before_flush")
def _fence_worker_flush(
    db: Session,
    _flush_context: object,
    _instances: object,
) -> None:
    if db.new or db.dirty or db.deleted:
        require_current_task_lease(db)


@event.listens_for(Session, "before_commit")
def _fence_worker_commit(db: Session) -> None:
    # Core UPDATE statements can bypass the ORM flush collections. The commit
    # hook keeps those transactions behind the same fencing boundary.
    require_current_task_lease(db)


@event.listens_for(Session, "after_transaction_end")
def _clear_worker_fence(db: Session, transaction: object) -> None:
    if getattr(transaction, "parent", None) is None:
        db.info.pop(_SESSION_FENCE_KEY, None)
