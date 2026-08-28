"""Durable single-target image generation handler."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

from app.assets.contracts import IMAGE_CANDIDATE_TASK_TYPE
from app.db import SessionLocal
from app.models import GenerationTask
from app.platform.media import get_media_store
from app.platform.media.store import suffix_for_mime
from app.platform.tasks.types import (
    SubmissionState,
    TaskExecutionError,
    TaskLane,
    TaskStatus,
)
from app.providers.defaults import provider_registry
from app.providers.types import (
    ProviderAsset,
    ProviderExecutionMode,
    ProviderRequest,
    ProviderResponse,
    ProviderStatus,
    ProviderType,
)
from app.services.image_generation_service import persist_image_candidate_task_result
from app.services.image_provider_profile_service import resolve_image_provider_selection
from app.services.media_generation_support import sanitize_large_payload
from app.services.workflow_state_service import mark_stage_failed
from app.services.stage_run_service import finish_latest_stage_run


logger = logging.getLogger(__name__)


class ImageCandidateTaskHandler:
    task_type = IMAGE_CANDIDATE_TASK_TYPE
    lane = TaskLane.IMAGE

    def execute(self, task_id: str) -> None:
        try:
            self._execute(task_id)
        except TaskExecutionError as exc:
            self._record_final_failure(task_id, exc)
            raise
        except Exception as exc:
            with SessionLocal() as db:
                task = db.get(GenerationTask, task_id)
                accepted = bool(task and task.provider_task_id)
            normalized = TaskExecutionError(
                "image_task_failed",
                str(exc) or exc.__class__.__name__,
                retryable=False,
                submission_state=(
                    SubmissionState.ACCEPTED if accepted else SubmissionState.UNKNOWN
                ),
            )
            self._record_final_failure(task_id, normalized)
            raise normalized from exc

    def _execute(self, task_id: str) -> None:
        with SessionLocal() as db:
            task = db.get(GenerationTask, task_id)
            if task is None:
                raise TaskExecutionError("task_not_found", "Image task no longer exists")
            try:
                request = _provider_request_from_task(task)
                provider = _provider_from_task(db, task)
            except Exception as exc:
                raise TaskExecutionError(
                    "image_task_input_invalid",
                    str(exc) or "Image task input/provider is invalid",
                    submission_state=SubmissionState.NOT_SUBMITTED,
                ) from exc
            provider_task_id = task.provider_task_id
            raw_response = dict(task.raw_response or {})
            if not provider_task_id and raw_response.get("submission_attempted_at"):
                raise TaskExecutionError(
                    "provider_submission_uncertain",
                    "Provider submission was interrupted before its result was stored",
                    submission_state=SubmissionState.UNKNOWN,
                    raw_response=raw_response,
                )

        if provider_task_id:
            response = self._poll(provider, task_id, provider_task_id)
        else:
            self._mark_submission_attempt(task_id)
            try:
                response = provider.submit(request)
            except Exception as exc:
                raise TaskExecutionError(
                    "image_provider_submission_failed",
                    str(exc) or "Image provider submission failed",
                    submission_state=SubmissionState.UNKNOWN,
                ) from exc
            self._store_submission(task_id, response)

        if self._cancel_requested(task_id):
            _delete_temporary_provider_assets(response)
            self.cancel(task_id)
            return
        if response.status in {ProviderStatus.QUEUED, ProviderStatus.RUNNING}:
            self._wait_for_provider(task_id, response)
            return
        if response.status == ProviderStatus.CANCELLED:
            self._mark_provider_cancelled(task_id, response)
            return
        if response.status in {ProviderStatus.FAILED, ProviderStatus.TIMEOUT}:
            error = _execution_error(response, accepted=bool(response.provider_task_id))
            if (
                error.retryable
                and error.submission_state == SubmissionState.NOT_SUBMITTED
            ):
                self._clear_submission_attempt(task_id)
            raise error
        if response.status != ProviderStatus.SUCCEEDED:
            raise TaskExecutionError(
                "image_provider_invalid_status",
                f"Unexpected provider status: {response.status}",
                submission_state=(
                    SubmissionState.ACCEPTED
                    if response.provider_task_id
                    else SubmissionState.UNKNOWN
                ),
            )

        if response.execution_mode == ProviderExecutionMode.ASYNC or not response.assets:
            remote_task_id = str(response.provider_task_id or provider_task_id or "")
            if not remote_task_id:
                raise TaskExecutionError(
                    "image_provider_task_id_missing",
                    "Async image provider did not return a task ID",
                    submission_state=SubmissionState.UNKNOWN,
                )
            with SessionLocal() as db:
                task = db.get(GenerationTask, task_id)
                if task is None:
                    return
                context = dict(task.raw_response or {})
                task.status = TaskStatus.RUNNING.value
                task.progress = max(task.progress, 75)
                task.progress_label = "正在获取 Provider 结果"
                db.add(task)
                db.commit()
            try:
                response = provider.fetch_result(
                    remote_task_id,
                    request=request,
                    provider_context=(
                        context.get("provider_submission")
                        if isinstance(context.get("provider_submission"), dict)
                        else {}
                    ),
                )
            except (OSError, TimeoutError) as exc:
                self._defer_provider_check(task_id, exc)
                return
            if response.status in {ProviderStatus.QUEUED, ProviderStatus.RUNNING}:
                self._wait_for_provider(task_id, response)
                return
            if response.status != ProviderStatus.SUCCEEDED or not response.assets:
                raise _execution_error(response, accepted=True)

        response = _materialize_provider_image_files(task_id, request.project_id, response)
        with SessionLocal() as db:
            task = db.get(GenerationTask, task_id)
            if task is None:
                _delete_materialized_assets(response)
                return
            if task.cancel_requested_at is not None or task.status == TaskStatus.CANCELLING.value:
                _delete_materialized_assets(response)
                return
            try:
                persist_image_candidate_task_result(db, task, response)
            except Exception:
                db.rollback()
                _delete_materialized_assets(response)
                raise

    def cancel(self, task_id: str) -> None:
        with SessionLocal() as db:
            task = db.get(GenerationTask, task_id)
            if task is None:
                return
            project_id = task.project_id
            parent_task_id = task.parent_task_id
            provider_task_id = task.provider_task_id
            if provider_task_id:
                try:
                    provider = _provider_from_task(db, task)
                except Exception:
                    provider = None
            else:
                provider = None
        if provider is not None and provider_task_id:
            try:
                provider.cancel(provider_task_id)
            except NotImplementedError:
                logger.info("Image provider %s does not support cancellation", provider.name)
            except Exception:
                logger.warning(
                    "Remote image cancellation failed for %s",
                    provider_task_id,
                    exc_info=True,
                )
        if parent_task_id is None:
            with SessionLocal() as db:
                finish_latest_stage_run(
                    db,
                    project_id,
                    "assets",
                    status="cancelled",
                    task_id=task_id,
                    error_code="image_task_cancelled",
                    error_message="图片任务已取消",
                )
                db.commit()

    def _poll(self, provider, task_id: str, provider_task_id: str) -> ProviderResponse:
        try:
            return provider.poll(provider_task_id)
        except (OSError, TimeoutError) as exc:
            self._defer_provider_check(task_id, exc)
            return ProviderResponse(
                status=ProviderStatus.RUNNING,
                provider_task_id=provider_task_id,
                execution_mode=ProviderExecutionMode.ASYNC,
                poll_after_sec=5,
            )

    @staticmethod
    def _mark_submission_attempt(task_id: str) -> None:
        with SessionLocal() as db:
            task = db.get(GenerationTask, task_id)
            if task is None:
                return
            task.raw_response = {
                **dict(task.raw_response or {}),
                "submission_attempted_at": datetime.now(timezone.utc).isoformat(),
            }
            task.progress = max(task.progress, 25)
            task.progress_label = "正在提交图片 Provider"
            db.add(task)
            db.commit()

    @staticmethod
    def _store_submission(task_id: str, response: ProviderResponse) -> None:
        with SessionLocal() as db:
            task = db.get(GenerationTask, task_id)
            if task is None:
                return
            accepted = (
                response.error is None
                or response.error.submission_state == SubmissionState.ACCEPTED
            )
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
                "Provider 已受理" if accepted else "Provider 提交失败"
            )
            db.add(task)
            db.commit()

    @staticmethod
    def _clear_submission_attempt(task_id: str) -> None:
        with SessionLocal() as db:
            task = db.get(GenerationTask, task_id)
            if task is None:
                return
            raw = dict(task.raw_response or {})
            raw.pop("submission_attempted_at", None)
            raw["last_not_submitted_response"] = raw.pop("provider_submission", {})
            task.raw_response = raw
            task.provider_task_id = None
            db.add(task)
            db.commit()

    @staticmethod
    def _wait_for_provider(task_id: str, response: ProviderResponse) -> None:
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
            task.progress_label = "等待图片 Provider 完成"
            task.raw_response = {
                **dict(task.raw_response or {}),
                "provider_poll": sanitize_large_payload(response.raw_response),
            }
            db.add(task)
            db.commit()

    @staticmethod
    def _defer_provider_check(task_id: str, exc: Exception) -> None:
        with SessionLocal() as db:
            task = db.get(GenerationTask, task_id)
            if task is None:
                return
            result = dict(task.result_payload or {})
            result["provider_poll_error_count"] = int(
                result.get("provider_poll_error_count") or 0
            ) + 1
            result["last_provider_poll_error"] = str(exc)
            task.result_payload = result
            task.status = TaskStatus.WAITING_PROVIDER.value
            task.available_at = datetime.now(timezone.utc) + timedelta(seconds=5)
            task.progress_label = "图片 Provider 暂时不可达，稍后继续轮询"
            db.add(task)
            db.commit()

    @staticmethod
    def _mark_provider_cancelled(task_id: str, response: ProviderResponse) -> None:
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
    def _record_final_failure(task_id: str, exc: TaskExecutionError) -> None:
        with SessionLocal() as db:
            task = db.get(GenerationTask, task_id)
            if task is None or task.parent_task_id is not None:
                return
            if (
                exc.retryable
                and exc.submission_state == SubmissionState.NOT_SUBMITTED
                and task.retry_count < task.max_retries
            ):
                return
            mark_stage_failed(
                db,
                task.project_id,
                "images",
                summary=str(exc),
                task_id=task.id,
                error_code=exc.code,
            )
            db.commit()


def _provider_request_from_task(task: GenerationTask) -> ProviderRequest:
    payload = task.input_payload if isinstance(task.input_payload, dict) else {}
    frozen = payload.get("provider_request")
    if not isinstance(frozen, dict):
        raise ValueError("Image task does not contain a frozen provider request")
    return ProviderRequest(
        project_id=task.project_id,
        task_id=f"{task.id}:image-candidate",
        model=str(frozen.get("model") or task.model or ""),
        prompt=str(frozen.get("prompt") or ""),
        negative_prompt=(
            str(frozen.get("negative_prompt"))
            if frozen.get("negative_prompt") is not None
            else None
        ),
        references=[str(value) for value in frozen.get("references") or []],
        params=dict(frozen.get("params") or {}),
        metadata=dict(frozen.get("metadata") or {}),
    )


def _provider_from_task(db, task: GenerationTask):
    payload = task.input_payload if isinstance(task.input_payload, dict) else {}
    profile_id = str(payload.get("image_provider_profile_id") or "") or None
    if profile_id:
        selection = resolve_image_provider_selection(db, profile_id)
        provider = selection.provider
    else:
        provider = provider_registry.get(ProviderType.IMAGE, task.provider)
    if provider.name != task.provider:
        raise ValueError(
            f"Frozen image provider {task.provider!r} no longer matches profile {provider.name!r}"
        )
    return provider


def _execution_error(response: ProviderResponse, *, accepted: bool) -> TaskExecutionError:
    error = response.error
    return TaskExecutionError(
        code=error.error_code if error else "image_provider_failed",
        message=error.error_message if error else "Image provider failed",
        retryable=bool(error and error.is_retryable),
        submission_state=(
            error.submission_state
            if error is not None
            else SubmissionState.ACCEPTED if accepted else SubmissionState.UNKNOWN
        ),
        raw_response=dict(response.raw_response or {}),
    )


def _materialize_provider_image_files(
    task_id: str,
    project_id: str,
    response: ProviderResponse,
) -> ProviderResponse:
    store = get_media_store()
    assets: list[ProviderAsset] = []
    for index, asset in enumerate(response.assets, start=1):
        metadata = asset.metadata if isinstance(asset.metadata, dict) else {}
        source = _local_provider_path(asset.uri)
        if metadata.get("temporary_file") or (source is not None and source.is_file()):
            if source is None or not source.is_file():
                raise FileNotFoundError(asset.uri)
            suffix = source.suffix or suffix_for_mime(asset.mime_type, ".png")
            uri = store.put_file(
                project_id,
                source,
                namespace="assets/image-candidates",
                filename=f"{task_id}-{index}{suffix}",
            )
            if metadata.get("temporary_file"):
                source.unlink(missing_ok=True)
            assets.append(
                replace(
                    asset,
                    uri=uri,
                    metadata={**metadata, "temporary_file": False},
                )
            )
        else:
            assets.append(asset)
    return replace(response, assets=assets)


def _local_provider_path(uri: str) -> Path | None:
    if uri.startswith("file://"):
        return Path(unquote(urlparse(uri).path)).expanduser()
    if "://" in uri or uri.startswith("data:"):
        return None
    return Path(uri).expanduser()


def _delete_temporary_provider_assets(response: ProviderResponse) -> None:
    for asset in response.assets:
        metadata = asset.metadata if isinstance(asset.metadata, dict) else {}
        if not metadata.get("temporary_file"):
            continue
        source = _local_provider_path(asset.uri)
        if source is not None:
            source.unlink(missing_ok=True)


def _delete_materialized_assets(response: ProviderResponse) -> None:
    store = get_media_store()
    for asset in response.assets:
        store.delete(asset.uri)
