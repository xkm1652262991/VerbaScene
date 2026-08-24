from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from app.db import SessionLocal
from app.core.config import settings
from app.models import Asset, GenerationTask, Shot
from app.platform.media import get_media_store
from app.platform.tasks.types import SubmissionState, TaskExecutionError, TaskLane, TaskStatus
from app.providers.defaults import provider_registry
from app.providers.types import ProviderExecutionMode, ProviderResponse, ProviderStatus, ProviderType
from app.production.video_candidate_persistence import persist_video_candidate_task_result
from app.production.video_request_compiler import compile_video_candidate_task_input
from app.services.media_generation_support import sanitize_large_payload
from app.services.stage_run_service import finish_latest_stage_run
from app.services.video_generation_service import VIDEO_CANDIDATE_TASK_TYPE
from app.services.workflow_state_service import mark_stage_failed
from app.production.video_provider_executor import (
    fetch_provider_result,
    persist_provider_temp_files,
    provider_request_from_task,
)


logger = logging.getLogger(__name__)


class VideoCandidateTaskHandler:
    task_type = VIDEO_CANDIDATE_TASK_TYPE
    lane = TaskLane.VIDEO

    def execute(self, task_id: str) -> None:
        try:
            self._execute(task_id)
        except TaskExecutionError as exc:
            self._record_failure(task_id, exc)
            raise
        except Exception as exc:
            with SessionLocal() as db:
                task = db.get(GenerationTask, task_id)
                accepted = bool(task and task.provider_task_id)
            normalized = TaskExecutionError(
                "video_task_failed",
                str(exc) or exc.__class__.__name__,
                retryable=False,
                submission_state=(
                    SubmissionState.ACCEPTED
                    if accepted
                    else SubmissionState.UNKNOWN
                ),
            )
            self._record_failure(task_id, normalized)
            raise normalized from exc

    def _execute(self, task_id: str) -> None:
        self._freeze_legacy_input(task_id)
        with SessionLocal() as db:
            task = db.get(GenerationTask, task_id)
            if task is None:
                raise TaskExecutionError("task_not_found", "Video task no longer exists")
            try:
                request = provider_request_from_task(task)
                provider = provider_registry.get(ProviderType.VIDEO, task.provider)
            except Exception as exc:
                raise TaskExecutionError(
                    "video_task_input_invalid",
                    str(exc) or "Video task input/provider is invalid",
                    submission_state=SubmissionState.NOT_SUBMITTED,
                ) from exc
            provider_task_id = task.provider_task_id
            raw_response = dict(task.raw_response or {})
            if not provider_task_id and raw_response.get("submission_attempted_at"):
                raise TaskExecutionError(
                    "provider_submission_uncertain",
                    "Provider submission was interrupted before its task ID was stored",
                    submission_state=SubmissionState.UNKNOWN,
                    raw_response=raw_response,
                )
            if provider_task_id and _provider_deadline_expired(raw_response, provider.name):
                raise TaskExecutionError(
                    "video_provider_timeout",
                    "Video provider task exceeded its configured deadline",
                    submission_state=SubmissionState.ACCEPTED,
                    raw_response=raw_response,
                )

        if provider_task_id:
            response = self._poll(provider, task_id, provider_task_id)
        else:
            self._mark_submission_attempt(task_id)
            response = provider.submit(request)
            self._store_submission(task_id, response)

        if self._cancel_requested(task_id):
            self.cancel(task_id)
            return

        if response.status in {ProviderStatus.QUEUED, ProviderStatus.RUNNING}:
            self._wait_for_provider(task_id, response)
            return
        if response.status == ProviderStatus.CANCELLED:
            self._mark_provider_cancelled(task_id, response)
            return
        if response.status in {ProviderStatus.FAILED, ProviderStatus.TIMEOUT}:
            raise _execution_error(response, accepted=bool(response.provider_task_id))

        if response.status != ProviderStatus.SUCCEEDED:
            raise TaskExecutionError(
                "video_provider_invalid_status",
                f"Unexpected provider status: {response.status}",
                submission_state=SubmissionState.ACCEPTED if response.provider_task_id else SubmissionState.UNKNOWN,
            )

        if response.execution_mode == ProviderExecutionMode.ASYNC or not response.assets:
            with SessionLocal() as db:
                current = db.get(GenerationTask, task_id)
                if current is None:
                    raise TaskExecutionError("task_not_found", "Video task no longer exists")
                current_provider_task_id = str(current.provider_task_id or response.provider_task_id)
                provider_context = dict(current.raw_response or {})
                self._mark_fetching(db, current)
                db.commit()
            try:
                response = fetch_provider_result(
                    provider,
                    current_provider_task_id,
                    request,
                    provider_context,
                )
            except (OSError, TimeoutError) as exc:
                self._defer_provider_check(task_id, exc)
                return
            if response.status in {ProviderStatus.QUEUED, ProviderStatus.RUNNING}:
                self._wait_for_provider(task_id, response)
                return
            if response.status != ProviderStatus.SUCCEEDED or not response.assets:
                raise _execution_error(response, accepted=True)

        response = persist_provider_temp_files(
            request.project_id,
            task_id,
            response,
        )
        with SessionLocal() as db:
            task = db.get(GenerationTask, task_id)
            if task is None:
                _delete_response_assets(response)
                return
            if task.cancel_requested_at is not None or task.status == TaskStatus.CANCELLING.value:
                _delete_response_assets(response)
                return
            persist_video_candidate_task_result(db, task, response)

    def _freeze_legacy_input(self, task_id: str) -> None:
        """Upgrade a pre-runtime queued task before its first provider call.

        Legacy queued rows only captured a few shot fields. They are frozen once
        on first claim. Legacy rows that may already have submitted remotely are
        failed by the data migration and never reach this compatibility path.
        """
        with SessionLocal() as db:
            task = db.get(GenerationTask, task_id)
            if task is None:
                raise TaskExecutionError("task_not_found", "Video task no longer exists")
            existing = task.input_payload if isinstance(task.input_payload, dict) else {}
            if isinstance(existing.get("provider_request"), dict):
                return
            if (task.raw_response or {}).get("submission_attempted_at"):
                return
            shot_id = str(existing.get("shot_id") or "")
            shot = db.get(Shot, shot_id) if shot_id else None
            if shot is None:
                raise TaskExecutionError(
                    "legacy_video_input_missing",
                    "Legacy video task shot no longer exists",
                    submission_state=SubmissionState.NOT_SUBMITTED,
                )
            source_asset_id = str(existing.get("source_asset_id") or "")
            source_asset = db.get(Asset, source_asset_id) if source_asset_id else None
            try:
                payload, provider = compile_video_candidate_task_input(
                    db,
                    shot,
                    duration_mode=str(existing.get("duration_mode") or "") or None,
                    duration_sec=_optional_decimal(existing.get("duration_sec")),
                    video_prompt=(
                        str(existing.get("video_prompt"))
                        if existing.get("video_prompt") is not None
                        else None
                    ),
                    source_asset=source_asset,
                    update_shot=False,
                    provider_name=task.provider,
                    model=task.model,
                )
            except Exception as exc:
                raise TaskExecutionError(
                    "legacy_video_snapshot_failed",
                    str(exc) or "Legacy video task could not be frozen",
                    submission_state=SubmissionState.NOT_SUBMITTED,
                ) from exc
            payload["operation"] = "legacy_migrated"
            payload["legacy_input_payload"] = existing
            task.input_payload = payload
            task.provider = provider.name
            task.model = task.model or provider.model
            task.raw_response = {
                **dict(task.raw_response or {}),
                "legacy_input_frozen_at": datetime.now(timezone.utc).isoformat(),
            }
            db.add(task)
            db.commit()

    def cancel(self, task_id: str) -> None:
        with SessionLocal() as db:
            task = db.get(GenerationTask, task_id)
            if task is None:
                return
            project_id = task.project_id
            parent_task_id = task.parent_task_id
            if not task.provider_task_id:
                self._record_cancelled_stage(task_id, project_id, parent_task_id)
                return
            provider = provider_registry.get(ProviderType.VIDEO, task.provider)
            provider_task_id = task.provider_task_id
        try:
            provider.cancel(provider_task_id)
        except NotImplementedError:
            logger.info("Provider %s does not support cancellation", provider.name)
        except Exception:
            logger.warning(
                "Remote cancellation failed for provider task %s",
                provider_task_id,
                exc_info=True,
            )
        self._record_cancelled_stage(task_id, project_id, parent_task_id)

    def _poll(self, provider: object, task_id: str, provider_task_id: str) -> ProviderResponse:
        try:
            return provider.poll(provider_task_id)
        except (OSError, TimeoutError) as exc:
            self._defer_provider_check(task_id, exc)
            return ProviderResponse(
                status=ProviderStatus.WAITING if hasattr(ProviderStatus, "WAITING") else ProviderStatus.RUNNING,
                provider_task_id=provider_task_id,
            )

    def _mark_submission_attempt(self, task_id: str) -> None:
        with SessionLocal() as db:
            task = db.get(GenerationTask, task_id)
            if task is None:
                return
            task.raw_response = {
                **dict(task.raw_response or {}),
                "submission_attempted_at": datetime.now(timezone.utc).isoformat(),
            }
            task.progress = max(task.progress, 25)
            task.progress_label = "正在提交 Provider"
            db.add(task)
            db.commit()

    def _store_submission(self, task_id: str, response: ProviderResponse) -> None:
        with SessionLocal() as db:
            task = db.get(GenerationTask, task_id)
            if task is None:
                return
            accepted = not response.error or response.error.submission_state == SubmissionState.ACCEPTED
            if response.provider_task_id and accepted:
                task.provider_task_id = response.provider_task_id
            task.raw_response = {
                **dict(task.raw_response or {}),
                "provider_submission": sanitize_large_payload(response.raw_response),
                "provider_submission_status": response.status.value,
                "provider_accepted_at": (
                    datetime.now(timezone.utc).isoformat()
                    if accepted and response.provider_task_id
                    else None
                ),
            }
            task.progress = max(task.progress, 40)
            task.progress_label = (
                "Provider 已受理"
                if accepted and response.provider_task_id
                else "Provider 提交失败"
            )
            db.add(task)
            db.commit()

    def _wait_for_provider(self, task_id: str, response: ProviderResponse) -> None:
        delay = max(1, int(response.poll_after_sec or 2))
        with SessionLocal() as db:
            task = db.get(GenerationTask, task_id)
            if task is None:
                return
            if task.cancel_requested_at is not None or task.status == TaskStatus.CANCELLING.value:
                return
            task.provider_task_id = response.provider_task_id or task.provider_task_id
            task.status = TaskStatus.WAITING_PROVIDER.value
            task.available_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
            task.progress = max(task.progress, 50)
            task.progress_label = "等待 Provider 完成"
            task.raw_response = {
                **dict(task.raw_response or {}),
                "provider_poll": sanitize_large_payload(response.raw_response),
            }
            db.add(task)
            db.commit()

    def _defer_provider_check(self, task_id: str, exc: Exception) -> None:
        with SessionLocal() as db:
            task = db.get(GenerationTask, task_id)
            if task is None:
                return
            result = dict(task.result_payload or {})
            result["provider_poll_error_count"] = int(result.get("provider_poll_error_count") or 0) + 1
            result["last_provider_poll_error"] = str(exc)
            task.result_payload = result
            task.status = TaskStatus.WAITING_PROVIDER.value
            task.available_at = datetime.now(timezone.utc) + timedelta(seconds=5)
            task.progress_label = "Provider 暂时不可达，稍后继续轮询"
            db.add(task)
            db.commit()

    def _mark_provider_cancelled(self, task_id: str, response: ProviderResponse) -> None:
        with SessionLocal() as db:
            task = db.get(GenerationTask, task_id)
            if task is None:
                return
            task.status = TaskStatus.CANCELLING.value
            task.cancel_requested_at = task.cancel_requested_at or datetime.now(timezone.utc)
            task.raw_response = {
                **dict(task.raw_response or {}),
                "provider_cancelled": sanitize_large_payload(response.raw_response),
            }
            db.add(task)
            db.commit()

    @staticmethod
    def _mark_fetching(db, task: GenerationTask) -> None:
        task.status = TaskStatus.RUNNING.value
        task.progress = max(task.progress, 75)
        task.progress_label = "正在获取 Provider 结果"
        db.add(task)

    @staticmethod
    def _cancel_requested(task_id: str) -> bool:
        with SessionLocal() as db:
            task = db.get(GenerationTask, task_id)
            return bool(
                task
                and (
                    task.cancel_requested_at is not None
                    or task.status == TaskStatus.CANCELLING.value
                )
            )

    @staticmethod
    def _record_failure(task_id: str, exc: Exception) -> None:
        with SessionLocal() as db:
            task = db.get(GenerationTask, task_id)
            if task is None or task.parent_task_id is not None:
                return
            code = exc.code if isinstance(exc, TaskExecutionError) else "video_task_failed"
            mark_stage_failed(
                db,
                task.project_id,
                "videos",
                summary=str(exc),
                error_code=code,
            )
            db.commit()

    @staticmethod
    def _record_cancelled_stage(
        task_id: str,
        project_id: str,
        parent_task_id: str | None,
    ) -> None:
        if parent_task_id is not None:
            return
        with SessionLocal() as db:
            finish_latest_stage_run(
                db,
                project_id,
                "production",
                status="cancelled",
                task_id=task_id,
                error_code="video_task_cancelled",
                error_message="视频任务已取消",
            )
            db.commit()


def _execution_error(response: ProviderResponse, *, accepted: bool) -> TaskExecutionError:
    error = response.error
    return TaskExecutionError(
        code=error.error_code if error else "video_provider_failed",
        message=error.error_message if error else "Video provider failed",
        retryable=bool(error and error.is_retryable),
        submission_state=(
            error.submission_state
            if error is not None
            else SubmissionState.ACCEPTED if accepted else SubmissionState.UNKNOWN
        ),
        raw_response=dict(response.raw_response or {}),
    )


def _delete_response_assets(response: ProviderResponse) -> None:
    store = get_media_store()
    for asset in response.assets:
        store.delete(asset.uri)


def _provider_deadline_expired(raw_response: dict, provider_name: str) -> bool:
    accepted_at_value = raw_response.get("provider_accepted_at")
    if not accepted_at_value:
        return False
    try:
        accepted_at = datetime.fromisoformat(str(accepted_at_value))
        if accepted_at.tzinfo is None:
            accepted_at = accepted_at.replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    timeout_by_provider = {
        "seedance2_api": settings.seedance2_api_job_timeout_sec,
        "ltx23_api": settings.ltx23_api_job_timeout_sec,
        "wan2_i2v_api": settings.wan_i2v_api_job_timeout_sec,
        "wan2_t2v_api": settings.wan_t2v_api_job_timeout_sec,
    }
    timeout_sec = int(timeout_by_provider.get(provider_name, settings.provider_timeout_sec))
    return datetime.now(timezone.utc) >= accepted_at + timedelta(seconds=max(1, timeout_sec))


def _optional_decimal(value: object) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid legacy duration: {value!r}") from exc
