from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import threading
import unittest

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.db.session import create_database_engine, initialize_database
from app.models import GenerationTask, Project
from app.platform.tasks.lease import TaskLeaseLostError, task_execution_lease
from app.platform.tasks.repository import TaskConflictError, TaskRepository
from app.platform.tasks.runtime import LocalTaskRuntime
from app.platform.tasks.types import (
    SubmissionState,
    TaskExecutionError,
    TaskLane,
    TaskStatus,
)
from app.services.task_command_service import retry_task


class UnifiedTaskRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.engine = create_database_engine(
            f"sqlite+pysqlite:///{(Path(self.temp_dir.name) / 'tasks.sqlite3').as_posix()}"
        )
        initialize_database(self.engine)
        self.session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            autocommit=False,
        )
        with self.session_factory() as db:
            project = Project(title="统一任务测试")
            db.add(project)
            db.commit()
            self.project_id = project.id

    def tearDown(self):
        self.engine.dispose()
        self.temp_dir.cleanup()

    def test_two_sessions_cannot_create_the_same_active_resource(self):
        barrier = threading.Barrier(2)

        def create() -> tuple[str, str | None]:
            with self.session_factory() as db:
                barrier.wait()
                try:
                    result = TaskRepository().create(
                        db,
                        project_id=self.project_id,
                        task_type="shot_video_candidate_generation",
                        resource_key="shot:one:video",
                        input_payload={"shot_id": "one"},
                    )
                    db.commit()
                    return "created", result.task.id
                except TaskConflictError as exc:
                    db.rollback()
                    return "conflict", exc.task_id

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(lambda _value: create(), range(2)))

        self.assertEqual(sorted(value for value, _task_id in outcomes), ["conflict", "created"])
        with self.session_factory() as db:
            tasks = list(db.scalars(select(GenerationTask)).all())
            self.assertEqual(len(tasks), 1)

    def test_idempotency_key_returns_the_original_task(self):
        with self.session_factory() as db:
            repository = TaskRepository()
            first = repository.create(
                db,
                project_id=self.project_id,
                task_type="script_generation",
                resource_key=f"project:{self.project_id}:script",
                idempotency_key="same-request",
                input_payload={"version": 1},
            )
            db.commit()
            second = repository.create(
                db,
                project_id=self.project_id,
                task_type="script_generation",
                resource_key=f"project:{self.project_id}:script",
                idempotency_key="same-request",
                input_payload={"version": 2},
            )

            self.assertTrue(first.created)
            self.assertFalse(second.created)
            self.assertEqual(first.task.id, second.task.id)
            self.assertEqual(second.task.input_payload, {"version": 1})

    def test_claim_respects_lease_and_recovers_only_after_expiry(self):
        repository = TaskRepository()
        with self.session_factory() as db:
            task = repository.create(
                db,
                project_id=self.project_id,
                task_type="script_generation",
                resource_key=f"project:{self.project_id}:script",
                input_payload={},
            ).task
            db.commit()
            task_id = task.id
        now = datetime.now(timezone.utc) + timedelta(milliseconds=10)

        with self.session_factory() as db:
            claimed = repository.claim_next(
                db,
                task_types=("script_generation",),
                owner="worker-one",
                lease_seconds=60,
                now=now,
            )
            db.commit()
            self.assertEqual(claimed.id, task_id)

        with self.session_factory() as db:
            self.assertIsNone(
                repository.claim_next(
                    db,
                    task_types=("script_generation",),
                    owner="worker-two",
                    lease_seconds=60,
                    now=now + timedelta(seconds=30),
                )
            )

        with self.session_factory() as db:
            recovered = repository.claim_next(
                db,
                task_types=("script_generation",),
                owner="worker-two",
                lease_seconds=60,
                now=now + timedelta(seconds=61),
            )
            db.commit()
            self.assertEqual(recovered.id, task_id)
            self.assertEqual(recovered.lease_owner, "worker-two")

    def test_heartbeat_extends_the_current_lease(self):
        repository = TaskRepository()
        with self.session_factory() as db:
            task = repository.create(
                db,
                project_id=self.project_id,
                task_type="script_generation",
                resource_key=f"project:{self.project_id}:script",
                input_payload={},
            ).task
            db.commit()
            task_id = task.id
        now = datetime.now(timezone.utc) + timedelta(milliseconds=10)
        with self.session_factory() as db:
            repository.claim_next(
                db,
                task_types=("script_generation",),
                owner="worker",
                lease_seconds=10,
                now=now,
            )
            db.commit()
        with self.session_factory() as db:
            self.assertTrue(
                repository.heartbeat(
                    db,
                    task_id=task_id,
                    owner="worker",
                    lease_seconds=10,
                    now=now + timedelta(seconds=8),
                )
            )
            db.commit()
            task = db.get(GenerationTask, task_id)
            self.assertGreaterEqual(task.lease_expires_at.replace(tzinfo=timezone.utc), now + timedelta(seconds=18))

    def test_reclaimed_task_rejects_stale_writes_even_for_the_same_worker(self):
        repository = TaskRepository()
        with self.session_factory() as db:
            task = repository.create(
                db,
                project_id=self.project_id,
                task_type="script_generation",
                resource_key=f"project:{self.project_id}:script",
                input_payload={},
            ).task
            db.commit()
            task_id = task.id
        now = datetime.now(timezone.utc) + timedelta(milliseconds=10)

        with self.session_factory() as db:
            first = repository.claim_next(
                db,
                task_types=("script_generation",),
                owner="reused-worker-name",
                lease_seconds=1,
                now=now,
            )
            db.commit()
            first_token = first.lease_token

        stale_db = self.session_factory()
        try:
            stale_task = stale_db.get(GenerationTask, task_id)
            with self.session_factory() as db:
                second = repository.claim_next(
                    db,
                    task_types=("script_generation",),
                    owner="reused-worker-name",
                    lease_seconds=30,
                    now=now + timedelta(seconds=2),
                )
                db.commit()
                second_token = second.lease_token

            self.assertNotEqual(first_token, second_token)
            self.assertFalse(
                repository.finish(
                    stale_db,
                    stale_task,
                    status=TaskStatus.SUCCEEDED,
                    progress_label="stale completion",
                )
            )
            stale_db.commit()

            with task_execution_lease(
                task_id,
                "reused-worker-name",
                str(first_token),
            ):
                with self.session_factory() as db:
                    current = db.get(GenerationTask, task_id)
                    current.progress_label = "stale checkpoint"
                    db.add(current)
                    with self.assertRaises(TaskLeaseLostError):
                        db.commit()
                    db.rollback()
        finally:
            stale_db.close()

        with self.session_factory() as db:
            current = db.get(GenerationTask, task_id)
            self.assertEqual(current.status, TaskStatus.RUNNING.value)
            self.assertEqual(current.lease_token, second_token)
            self.assertNotEqual(current.progress_label, "stale checkpoint")

    def test_cancel_and_retry_release_and_reacquire_dedupe_key(self):
        repository = TaskRepository()
        with self.session_factory() as db:
            task = repository.create(
                db,
                project_id=self.project_id,
                task_type="script_generation",
                resource_key=f"project:{self.project_id}:script",
                input_payload={"snapshot": True},
            ).task
            repository.request_cancel(db, task)
            db.commit()
            self.assertEqual(task.status, TaskStatus.CANCELLED.value)
            self.assertIsNone(task.active_dedupe_key)

            retry = repository.retry(
                db,
                task,
                allowed_task_types={"script_generation"},
            ).task
            db.commit()
            self.assertEqual(retry.status, TaskStatus.QUEUED.value)
            self.assertEqual(retry.retry_of_task_id, task.id)
            self.assertEqual(retry.input_payload, task.input_payload)
            self.assertIsNotNone(retry.active_dedupe_key)

    def test_manual_retry_rejects_task_types_without_a_runtime_handler(self):
        repository = TaskRepository()
        with self.session_factory() as db:
            task = repository.create(
                db,
                project_id=self.project_id,
                task_type="reference_image_generation",
                resource_key="project:images",
                input_payload={"snapshot": True},
            ).task
            repository.finish(
                db,
                task,
                status=TaskStatus.FAILED,
                progress_label="failed",
                error_code="test_failure",
                error_message="test failure",
            )
            db.commit()

            with self.assertRaisesRegex(
                TaskConflictError,
                "does not support manual retry",
            ):
                retry_task(db, task)

            self.assertEqual(
                len(list(db.scalars(select(GenerationTask)).all())),
                1,
            )

    def test_idle_waiting_provider_task_is_claimed_for_cancellation(self):
        repository = TaskRepository()
        with self.session_factory() as db:
            task = repository.create(
                db,
                project_id=self.project_id,
                task_type="runtime-cancel",
                resource_key="runtime:cancel",
                input_payload={},
            ).task
            task.status = TaskStatus.WAITING_PROVIDER.value
            task.provider_task_id = "remote-task"
            task.lease_owner = None
            task.lease_expires_at = None
            repository.request_cancel(db, task)
            db.commit()
            task_id = task.id

        with self.session_factory() as db:
            claimed = repository.claim_next(
                db,
                task_types=("runtime-cancel",),
                owner="cancel-worker",
                lease_seconds=30,
            )
            db.commit()

            self.assertIsNotNone(claimed)
            self.assertEqual(claimed.id, task_id)
            self.assertEqual(claimed.status, TaskStatus.CANCELLING.value)
            self.assertEqual(claimed.lease_owner, "cancel-worker")

    def test_auto_retry_is_limited_to_not_submitted_errors(self):
        repository = TaskRepository()
        with self.session_factory() as db:
            task = repository.create(
                db,
                project_id=self.project_id,
                task_type="script_generation",
                resource_key=f"project:{self.project_id}:script",
                input_payload={},
                max_retries=1,
            ).task
            task.status = TaskStatus.RUNNING.value
            task.lease_owner = "worker"
            db.commit()
            task_id = task.id

        runtime = LocalTaskRuntime(session_factory=self.session_factory)
        runtime._handle_execution_error(
            task_id,
            "worker",
            TaskExecutionError(
                "provider_unavailable",
                "not accepted",
                retryable=True,
                submission_state=SubmissionState.NOT_SUBMITTED,
            ),
        )
        with self.session_factory() as db:
            task = db.get(GenerationTask, task_id)
            self.assertEqual(task.status, TaskStatus.QUEUED.value)
            self.assertEqual(task.retry_count, 1)
            task.status = TaskStatus.RUNNING.value
            task.lease_owner = "worker"
            db.commit()

        runtime._handle_execution_error(
            task_id,
            "worker",
            TaskExecutionError(
                "provider_unavailable",
                "not accepted again",
                retryable=True,
                submission_state=SubmissionState.NOT_SUBMITTED,
            ),
        )
        with self.session_factory() as db:
            task = db.get(GenerationTask, task_id)
            self.assertEqual(task.status, TaskStatus.FAILED.value)
            self.assertEqual(task.retry_count, 1)

    def test_uncertain_submission_never_retries(self):
        with self.session_factory() as db:
            task = TaskRepository().create(
                db,
                project_id=self.project_id,
                task_type="script_generation",
                resource_key=f"project:{self.project_id}:script",
                input_payload={},
            ).task
            task.status = TaskStatus.RUNNING.value
            task.lease_owner = "worker"
            db.commit()
            task_id = task.id

        runtime = LocalTaskRuntime(session_factory=self.session_factory)
        runtime._handle_execution_error(
            task_id,
            "worker",
            TaskExecutionError(
                "transport_error",
                "submission outcome is unknown",
                retryable=True,
                submission_state=SubmissionState.UNKNOWN,
            ),
        )
        with self.session_factory() as db:
            task = db.get(GenerationTask, task_id)
            self.assertEqual(task.status, TaskStatus.FAILED.value)
            self.assertEqual(task.error_code, "provider_submission_uncertain")
            self.assertEqual(task.retry_count, 0)

    def test_local_runtime_claims_and_finishes_a_task(self):
        completed = threading.Event()
        session_factory = self.session_factory

        class Handler:
            task_type = "runtime-test"
            lane = TaskLane.SCRIPT

            def execute(self, task_id: str) -> None:
                with session_factory() as db:
                    task = db.get(GenerationTask, task_id)
                    TaskRepository().finish(
                        db,
                        task,
                        status=TaskStatus.SUCCEEDED,
                        progress_label="done",
                    )
                    db.commit()
                completed.set()

            def cancel(self, task_id: str) -> None:
                _ = task_id

        with self.session_factory() as db:
            task = TaskRepository().create(
                db,
                project_id=self.project_id,
                task_type="runtime-test",
                resource_key="runtime:test",
                input_payload={},
            ).task
            db.commit()
            task_id = task.id

        runtime = LocalTaskRuntime(
            session_factory=self.session_factory,
            poll_interval_sec=0.05,
            lease_seconds=4,
            heartbeat_seconds=1,
        )
        runtime.register(Handler())
        runtime.start()
        try:
            self.assertTrue(completed.wait(2))
        finally:
            runtime.stop(timeout_sec=2)

        with self.session_factory() as db:
            task = db.get(GenerationTask, task_id)
            self.assertEqual(task.status, TaskStatus.SUCCEEDED.value)
            self.assertIsNone(task.lease_owner)
            self.assertIsNone(task.active_dedupe_key)

    def test_script_lane_worker_count_is_configurable(self):
        class Handler:
            task_type = "script-worker-count"
            lane = TaskLane.SCRIPT

            def execute(self, task_id: str) -> None:
                _ = task_id

            def cancel(self, task_id: str) -> None:
                _ = task_id

        runtime = LocalTaskRuntime(
            session_factory=self.session_factory,
            script_concurrency=2,
        )
        runtime.register(Handler())
        runtime.start()
        try:
            script_workers = [
                thread
                for thread in runtime._threads
                if thread.name.startswith("task-script-")
            ]
            self.assertEqual(len(script_workers), 2)
        finally:
            runtime.stop(timeout_sec=2)


if __name__ == "__main__":
    unittest.main()
