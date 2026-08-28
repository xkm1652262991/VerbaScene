from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from app.providers.types import SubmissionState


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_PROVIDER = "waiting_provider"
    WAITING_CHILDREN = "waiting_children"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


ACTIVE_TASK_STATUSES = frozenset(
    {
        TaskStatus.QUEUED.value,
        TaskStatus.RUNNING.value,
        TaskStatus.WAITING_PROVIDER.value,
        TaskStatus.WAITING_CHILDREN.value,
        TaskStatus.CANCELLING.value,
    }
)
TERMINAL_TASK_STATUSES = frozenset(
    {
        TaskStatus.SUCCEEDED.value,
        TaskStatus.FAILED.value,
        TaskStatus.CANCELLED.value,
    }
)


class TaskLane(StrEnum):
    SCRIPT = "script"
    IMAGE = "image"
    VIDEO = "video"
    MEDIA = "media"


@dataclass(frozen=True)
class TaskExecutionError(Exception):
    code: str
    message: str
    retryable: bool = False
    submission_state: SubmissionState = SubmissionState.NOT_SUBMITTED
    raw_response: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message


class TaskHandler(Protocol):
    task_type: str
    lane: TaskLane

    def execute(self, task_id: str) -> None:
        """Execute one checkpoint of a claimed task.

        Implementations own their short database transactions. A handler may
        finish the task or put it into a durable waiting state.
        """

    def cancel(self, task_id: str) -> None:
        """Best-effort remote cancellation before the local terminal state."""
