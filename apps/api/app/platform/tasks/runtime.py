from __future__ import annotations

import logging
import socket
from threading import Event, Lock, Thread, current_thread
from time import monotonic
from typing import Callable
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.db import SessionLocal
from app.models import GenerationTask
from app.platform.tasks.repository import TaskRepository
from app.platform.tasks.types import (
    SubmissionState,
    TaskExecutionError,
    TaskHandler,
    TaskLane,
    TaskStatus,
    TERMINAL_TASK_STATUSES,
)


logger = logging.getLogger(__name__)


class LocalTaskRuntime:
    """Lease-based local worker runtime with the database as source of truth."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        repository: TaskRepository | None = None,
        poll_interval_sec: float = 0.5,
        lease_seconds: int = 60,
        heartbeat_seconds: int = 15,
        video_concurrency: int = 2,
    ) -> None:
        self.session_factory = session_factory
        self.repository = repository or TaskRepository()
        self.poll_interval_sec = max(0.05, poll_interval_sec)
        self.lease_seconds = max(2, lease_seconds)
        self.heartbeat_seconds = max(1, min(heartbeat_seconds, self.lease_seconds // 2))
        self.video_concurrency = max(1, min(video_concurrency, 2))
        self.runtime_id = f"{socket.gethostname()}:{uuid4().hex[:12]}"
        self._handlers: dict[str, TaskHandler] = {}
        self._parent_reconcilers: dict[str, Callable[[Session, GenerationTask], None]] = {}
        self._started = False
        self._lock = Lock()
        self._stop_event = Event()
        self._wake_event = Event()
        self._threads: list[Thread] = []
        self._active: dict[int, tuple[str, str]] = {}

    def register(self, handler: TaskHandler) -> None:
        with self._lock:
            if self._started:
                raise RuntimeError("Task handlers must be registered before runtime start")
            if handler.task_type in self._handlers:
                raise ValueError(f"Duplicate task handler: {handler.task_type}")
            self._handlers[handler.task_type] = handler

    def register_parent_reconciler(
        self,
        task_type: str,
        reconciler: Callable[[Session, GenerationTask], None],
    ) -> None:
        with self._lock:
            if self._started:
                raise RuntimeError("Parent reconcilers must be registered before runtime start")
            if task_type in self._parent_reconcilers:
                raise ValueError(f"Duplicate parent reconciler: {task_type}")
            self._parent_reconcilers[task_type] = reconciler

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
            self._stop_event.clear()
            workers = [(TaskLane.SCRIPT, 1), (TaskLane.VIDEO, self.video_concurrency)]
            for lane, count in workers:
                if not any(handler.lane == lane for handler in self._handlers.values()):
                    continue
                for index in range(count):
                    thread = Thread(
                        target=self._worker_loop,
                        args=(lane, index + 1),
                        name=f"task-{lane.value}-{index + 1}",
                        daemon=True,
                    )
                    self._threads.append(thread)
                    thread.start()
            coordinator = Thread(
                target=self._coordinator_loop,
                name="task-parent-coordinator",
                daemon=True,
            )
            self._threads.append(coordinator)
            coordinator.start()
        self.wake()

    def wake(self) -> None:
        self._wake_event.set()

    def stop(self, *, timeout_sec: float = 30.0) -> None:
        with self._lock:
            if not self._started:
                return
            self._stop_event.set()
            self._wake_event.set()
            threads = list(self._threads)
        deadline = monotonic() + max(0, timeout_sec)
        for thread in threads:
            remaining = deadline - monotonic()
            if remaining <= 0:
                break
            thread.join(remaining)
        self._release_unfinished_leases()
        with self._lock:
            self._threads.clear()
            self._active.clear()
            self._started = False

    def _worker_loop(self, lane: TaskLane, index: int) -> None:
        owner = f"{self.runtime_id}:{lane.value}:{index}"
        task_types = tuple(
            task_type
            for task_type, handler in self._handlers.items()
            if handler.lane == lane
        )
        while not self._stop_event.is_set():
            task_id: str | None = None
            try:
                with self.session_factory() as db:
                    task = self.repository.claim_next(
                        db,
                        task_types=task_types,
                        owner=owner,
                        lease_seconds=self.lease_seconds,
                    )
                    db.commit()
                    task_id = task.id if task else None
                if task_id is None:
                    self._wake_event.wait(self.poll_interval_sec)
                    self._wake_event.clear()
                    continue
                with self._lock:
                    self._active[current_thread().ident or 0] = (task_id, owner)
                self._execute_claimed(task_id, owner)
            except Exception:
                logger.exception("Task worker %s failed while claiming/executing %s", owner, task_id)
            finally:
                with self._lock:
                    self._active.pop(current_thread().ident or 0, None)

    def _execute_claimed(self, task_id: str, owner: str) -> None:
        heartbeat_stop = Event()
        heartbeat = Thread(
            target=self._heartbeat_loop,
            args=(task_id, owner, heartbeat_stop),
            name=f"task-heartbeat-{task_id[:8]}",
            daemon=True,
        )
        heartbeat.start()
        try:
            with self.session_factory() as db:
                task = db.get(GenerationTask, task_id)
                handler = self._handlers.get(task.task_type) if task else None
                cancelling = bool(task and task.status == TaskStatus.CANCELLING.value)
            if task is None or handler is None:
                raise TaskExecutionError("task_handler_missing", "Task handler is not registered")
            if cancelling:
                handler.cancel(task_id)
                self._finish_cancelled(task_id, owner)
                return
            handler.execute(task_id)
            self._normalize_handler_checkpoint(task_id, owner)
        except TaskExecutionError as exc:
            self._handle_execution_error(task_id, owner, exc)
        except Exception as exc:
            self._handle_execution_error(
                task_id,
                owner,
                TaskExecutionError(
                    code="task_handler_failed",
                    message=str(exc) or exc.__class__.__name__,
                    retryable=False,
                    submission_state=SubmissionState.UNKNOWN,
                ),
            )
            logger.exception("Task handler failed for %s", task_id)
        finally:
            heartbeat_stop.set()
            heartbeat.join(min(1.0, self.heartbeat_seconds))

    def _normalize_handler_checkpoint(self, task_id: str, owner: str) -> None:
        with self.session_factory() as db:
            task = db.get(GenerationTask, task_id)
            if task is None:
                return
            if task.status == TaskStatus.CANCELLING.value:
                self.repository.finish(
                    db,
                    task,
                    status=TaskStatus.CANCELLED,
                    progress_label="任务已取消",
                )
            elif task.status in TERMINAL_TASK_STATUSES:
                task.active_dedupe_key = None
                task.lease_owner = None
                task.lease_expires_at = None
                db.add(task)
                db.flush()
            else:
                self.repository.release_lease(db, task_id=task_id, owner=owner)
            db.commit()
        self.wake()

    def _handle_execution_error(self, task_id: str, owner: str, exc: TaskExecutionError) -> None:
        with self.session_factory() as db:
            task = db.get(GenerationTask, task_id)
            if task is None or task.status in TERMINAL_TASK_STATUSES:
                return
            if task.cancel_requested_at is not None or task.status == TaskStatus.CANCELLING.value:
                self.repository.finish(
                    db,
                    task,
                    status=TaskStatus.CANCELLED,
                    progress_label="任务已取消",
                )
            elif (
                exc.retryable
                and exc.submission_state == SubmissionState.NOT_SUBMITTED
                and task.retry_count < task.max_retries
            ):
                task.retry_count += 1
                task.status = TaskStatus.QUEUED.value
                task.progress_label = "Provider 未受理，等待自动重试"
                task.error_code = exc.code
                task.error_message = exc.message
                task.raw_response = exc.raw_response
                task.lease_owner = None
                task.lease_expires_at = None
                db.add(task)
                db.flush()
            else:
                error_code = (
                    "provider_submission_uncertain"
                    if exc.submission_state == SubmissionState.UNKNOWN
                    else exc.code
                )
                self.repository.finish(
                    db,
                    task,
                    status=TaskStatus.FAILED,
                    progress_label="任务失败",
                    error_code=error_code,
                    error_message=exc.message,
                    raw_response=exc.raw_response,
                )
            db.commit()
        self.wake()

    def _finish_cancelled(self, task_id: str, owner: str) -> None:
        with self.session_factory() as db:
            task = db.get(GenerationTask, task_id)
            if task is not None and task.status not in TERMINAL_TASK_STATUSES:
                self.repository.finish(
                    db,
                    task,
                    status=TaskStatus.CANCELLED,
                    progress_label="任务已取消",
                )
            elif task is not None:
                self.repository.release_lease(db, task_id=task_id, owner=owner)
            db.commit()
        self.wake()

    def _heartbeat_loop(self, task_id: str, owner: str, stop: Event) -> None:
        while not stop.wait(self.heartbeat_seconds):
            try:
                with self.session_factory() as db:
                    alive = self.repository.heartbeat(
                        db,
                        task_id=task_id,
                        owner=owner,
                        lease_seconds=self.lease_seconds,
                    )
                    db.commit()
                if not alive:
                    return
            except Exception:
                logger.exception("Task heartbeat failed for %s", task_id)

    def _coordinator_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                with self.session_factory() as db:
                    parent_types = tuple(self._parent_reconcilers)
                    parents = list(
                        db.scalars(
                            select(GenerationTask)
                            .where(GenerationTask.task_type.in_(parent_types))
                            .where(
                                GenerationTask.status.in_(
                                    (TaskStatus.WAITING_CHILDREN.value, TaskStatus.CANCELLING.value)
                                )
                            )
                        ).all()
                    )
                    for parent in parents:
                        self._parent_reconcilers[parent.task_type](db, parent)
                    if parents:
                        db.commit()
            except Exception:
                logger.exception("Parent task reconciliation failed")
            self._wake_event.wait(self.poll_interval_sec)
            self._wake_event.clear()

    def _release_unfinished_leases(self) -> None:
        with self._lock:
            active = list(self._active.values())
        for task_id, owner in active:
            try:
                with self.session_factory() as db:
                    self.repository.release_lease(db, task_id=task_id, owner=owner)
                    db.commit()
            except Exception:
                logger.exception("Failed to release task lease during shutdown: %s", task_id)


_runtime: LocalTaskRuntime | None = None
_runtime_lock = Lock()


def get_task_runtime() -> LocalTaskRuntime:
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            _runtime = LocalTaskRuntime(
                session_factory=SessionLocal,
                poll_interval_sec=settings.task_poll_interval_sec,
                lease_seconds=settings.task_lease_sec,
                heartbeat_seconds=settings.task_heartbeat_sec,
                video_concurrency=settings.video_generation_concurrency,
            )
        return _runtime
