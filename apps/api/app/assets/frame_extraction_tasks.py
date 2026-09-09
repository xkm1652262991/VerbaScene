"""Creation and execution of durable video frame extraction tasks."""

from __future__ import annotations

import shutil
import tempfile
from decimal import Decimal
from pathlib import Path

from fastapi import HTTPException, status
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.assets.contracts import VIDEO_FRAME_EXTRACTION_TASK_TYPE
from app.core.config import settings
from app.db import SessionLocal
from app.models import Asset, AssetCandidate, GenerationTask
from app.platform.media import get_media_store
from app.platform.media.subprocess_runner import (
    MediaProcessCancelled,
    run_cancellable_media_process,
)
from app.platform.tasks.repository import TaskConflictError, TaskRepository
from app.platform.tasks.types import SubmissionState, TaskExecutionError, TaskLane, TaskStatus
from app.services.artifact_idempotency import artifact_completion_key
from app.services.asset_repository import next_candidate_version
from app.services.stage_run_service import finish_latest_stage_run
from app.services.task_service import begin_task_completion, mark_task_succeeded
from app.services.workflow_state_service import mark_stage_failed, mark_stage_ready, mark_stage_running


def create_video_frame_extraction_task(
    db: Session,
    asset_id: str,
    *,
    time_sec: Decimal = Decimal("0"),
    idempotency_key: str | None = None,
) -> GenerationTask:
    source = db.get(Asset, asset_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    if source.asset_type != "video" or source.entity_type != "shot" or not source.entity_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only shot video assets can be used for frame extraction",
        )
    if time_sec < 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Frame time must be greater than or equal to zero",
        )
    if source.duration_sec is not None and time_sec > source.duration_sec:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Frame time exceeds the video duration",
        )
    normalized_idempotency_key = (idempotency_key or "").strip()
    if normalized_idempotency_key:
        existing = db.scalar(
            select(GenerationTask)
            .where(GenerationTask.project_id == source.project_id)
            .where(GenerationTask.task_type == VIDEO_FRAME_EXTRACTION_TASK_TYPE)
            .where(GenerationTask.idempotency_key == normalized_idempotency_key)
        )
        if existing is not None:
            return existing
    version = next_candidate_version(
        db,
        source.project_id,
        "image",
        "shot",
        source.entity_id,
        "shot_storyboard",
    )
    time_value = _decimal_value(time_sec)
    try:
        creation = TaskRepository().create(
            db,
            project_id=source.project_id,
            task_type=VIDEO_FRAME_EXTRACTION_TASK_TYPE,
            resource_key=f"asset:{source.id}:frame:{time_value}",
            idempotency_key=normalized_idempotency_key or None,
            provider="local",
            model="ffmpeg",
            input_payload={
                "project_id": source.project_id,
                "source_asset_id": source.id,
                "asset_id": source.id,
                "shot_id": source.entity_id,
                "source_uri": source.uri,
                "source_script_id": source.source_script_id,
                "source_shot_batch_id": source.source_shot_batch_id,
                "source_asset_version": source.version,
                "time_sec": time_value,
                "candidate_version": version,
            },
            progress_label="等待视频截帧",
            max_retries=0,
        )
    except TaskConflictError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "frame_extraction_in_progress",
                "message": "该视频时间点已有活动截帧任务",
                "task_id": exc.task_id,
            },
        ) from exc
    if creation.created:
        mark_stage_running(db, source.project_id, "images", task_id=creation.task.id)
    db.commit()
    db.refresh(creation.task)
    return creation.task


class VideoFrameExtractionTaskHandler:
    task_type = VIDEO_FRAME_EXTRACTION_TASK_TYPE
    lane = TaskLane.MEDIA

    def execute(self, task_id: str) -> None:
        work_dir = _work_dir(task_id)
        output_path = work_dir / f"{task_id}.png"
        try:
            with SessionLocal() as db:
                task = db.get(GenerationTask, task_id)
                if task is None:
                    raise TaskExecutionError("task_not_found", "Frame task no longer exists")
                snapshot = dict(task.input_payload or {})
                task.progress = max(task.progress, 20)
                task.progress_label = "正在准备视频截帧"
                db.add(task)
                db.commit()
            source_uri = str(snapshot.get("source_uri") or "")
            try:
                source_path = get_media_store().resolve_local_path(source_uri)
            except (FileNotFoundError, ValueError) as exc:
                raise TaskExecutionError(
                    "frame_source_unavailable",
                    f"Frame source is unavailable: {source_uri}",
                    submission_state=SubmissionState.NOT_SUBMITTED,
                ) from exc
            shutil.rmtree(work_dir, ignore_errors=True)
            work_dir.mkdir(parents=True, exist_ok=True)
            args = [
                settings.ffmpeg_path,
                "-y",
                "-ss",
                str(snapshot.get("time_sec") or "0"),
                "-i",
                str(source_path),
                "-frames:v",
                "1",
                "-update",
                "1",
                str(output_path),
            ]
            self._update_progress(task_id, 40, "FFmpeg 正在提取视频帧")
            result = run_cancellable_media_process(
                args,
                cancel_requested=lambda: self._cancel_requested(task_id),
                timeout_sec=settings.provider_timeout_sec,
            )
            if result.returncode != 0:
                raise TaskExecutionError(
                    "video_frame_extraction_failed",
                    result.output_tail or "FFmpeg frame extraction failed",
                    submission_state=SubmissionState.NOT_SUBMITTED,
                )
            if not output_path.is_file() or output_path.stat().st_size == 0:
                raise TaskExecutionError(
                    "video_frame_extraction_missing_output",
                    "FFmpeg did not create a frame",
                    submission_state=SubmissionState.NOT_SUBMITTED,
                )
            if self._cancel_requested(task_id):
                return
            with Image.open(output_path) as image:
                width, height = image.size
            candidate_uri = get_media_store().put_file(
                str(snapshot.get("project_id") or ""),
                output_path,
                namespace="assets/extracted-frames",
                filename=f"{task_id}.png",
            )
            if self._cancel_requested(task_id):
                get_media_store().delete(candidate_uri)
                return
            with SessionLocal() as db:
                task = db.get(GenerationTask, task_id)
                if task is None:
                    get_media_store().delete(candidate_uri)
                    return
                if task.cancel_requested_at is not None or task.status == TaskStatus.CANCELLING.value:
                    get_media_store().delete(candidate_uri)
                    return
                try:
                    if not begin_task_completion(db, task):
                        get_media_store().delete(candidate_uri)
                        return
                    candidate = AssetCandidate(
                        project_id=task.project_id,
                        source_task_id=task.id,
                        completion_key=artifact_completion_key(
                            task.id,
                            artifact_kind="candidate",
                            candidate_type="extracted_frame",
                            asset_type="image",
                            asset_role="shot_storyboard",
                            entity_type="shot",
                            entity_id=str(snapshot.get("shot_id") or ""),
                        ),
                        source_script_id=snapshot.get("source_script_id"),
                        source_shot_batch_id=snapshot.get("source_shot_batch_id"),
                        candidate_type="extracted_frame",
                        asset_type="image",
                        asset_role="shot_storyboard",
                        entity_type="shot",
                        entity_id=str(snapshot.get("shot_id") or ""),
                        version=int(snapshot.get("candidate_version") or 1),
                        uri=candidate_uri,
                        mime_type="image/png",
                        width=width,
                        height=height,
                        provider="local",
                        model="ffmpeg",
                        prompt=(
                            f"Extract frame at {snapshot.get('time_sec')}s from "
                            f"video asset {snapshot.get('source_asset_id')}"
                        ),
                        raw_response={
                            "source_video_asset_id": snapshot.get("source_asset_id"),
                            "source_asset_version": snapshot.get("source_asset_version"),
                            "time_sec": snapshot.get("time_sec"),
                            "ffmpeg_args": args,
                        },
                        status="pending_review",
                    )
                    db.add(candidate)
                    db.flush()
                    mark_stage_ready(
                        db,
                        task.project_id,
                        "images",
                        task_id=task.id,
                        summary="视频帧候选已提取，等待确认入库",
                        metadata={"candidate_id": candidate.id},
                    )
                    mark_task_succeeded(
                        db,
                        task,
                        result_payload={
                            "candidate_ids": [candidate.id],
                            "candidate_count": 1,
                            "candidate_policy": "pending_review_before_asset",
                        },
                        raw_response={
                            **dict(task.raw_response or {}),
                            "ffmpeg_args": args,
                        },
                    )
                    db.add(task)
                    db.commit()
                except Exception:
                    db.rollback()
                    get_media_store().delete(candidate_uri)
                    raise
        except MediaProcessCancelled:
            return
        except TaskExecutionError as exc:
            self._record_failure(task_id, exc)
            raise
        except Exception as exc:
            normalized = TaskExecutionError(
                "video_frame_extraction_failed",
                str(exc) or exc.__class__.__name__,
                submission_state=SubmissionState.NOT_SUBMITTED,
            )
            self._record_failure(task_id, normalized)
            raise normalized from exc
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def cancel(self, task_id: str) -> None:
        shutil.rmtree(_work_dir(task_id), ignore_errors=True)
        with SessionLocal() as db:
            task = db.get(GenerationTask, task_id)
            if task is None:
                return
            finish_latest_stage_run(
                db,
                task.project_id,
                "assets",
                status="cancelled",
                task_id=task.id,
                error_code="frame_extraction_cancelled",
                error_message="视频截帧任务已取消",
            )
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
    def _update_progress(task_id: str, progress: int, label: str) -> None:
        with SessionLocal() as db:
            task = db.get(GenerationTask, task_id)
            if task is None:
                return
            task.progress = max(task.progress, progress)
            task.progress_label = label
            db.add(task)
            db.commit()

    @staticmethod
    def _record_failure(task_id: str, exc: TaskExecutionError) -> None:
        with SessionLocal() as db:
            task = db.get(GenerationTask, task_id)
            if task is None:
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


def _work_dir(task_id: str) -> Path:
    safe_task_id = "".join(character for character in task_id if character.isalnum() or character in "-_")
    if not safe_task_id:
        raise ValueError("Invalid frame extraction task ID")
    return Path(tempfile.gettempdir()) / "verbascene-frame-tasks" / safe_task_id


def _decimal_value(value: Decimal) -> str:
    return format(value.normalize(), "f")
