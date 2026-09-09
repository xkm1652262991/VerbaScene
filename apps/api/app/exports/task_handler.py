"""Single-concurrency FFmpeg export task handler."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from app.core.config import settings
from app.db import SessionLocal
from app.exports.contracts import PROJECT_EXPORT_TASK_TYPE
from app.exports.subtitle_alignment import align_export_subtitles
from app.models import GenerationTask
from app.platform.media import get_media_store
from app.platform.media.subprocess_runner import (
    MediaProcessCancelled,
    run_cancellable_media_process,
)
from app.platform.tasks.types import SubmissionState, TaskExecutionError, TaskLane, TaskStatus
from app.services.export_service import (
    build_export_ffmpeg_args_from_snapshot,
    persist_export_task_result,
)
from app.services.stage_run_service import finish_latest_stage_run
from app.services.workflow_state_service import mark_stage_failed


class ProjectExportTaskHandler:
    task_type = PROJECT_EXPORT_TASK_TYPE
    lane = TaskLane.MEDIA

    def execute(self, task_id: str) -> None:
        work_dir = _work_dir(task_id)
        output_path = work_dir / f"{task_id}.mp4"
        try:
            with SessionLocal() as db:
                task = db.get(GenerationTask, task_id)
                if task is None:
                    raise TaskExecutionError("task_not_found", "Export task no longer exists")
                snapshot = dict(task.input_payload or {})
                task.progress = max(task.progress, 15)
                task.progress_label = "正在准备导出时间线"
                db.add(task)
                db.commit()

            shutil.rmtree(work_dir, ignore_errors=True)
            work_dir.mkdir(parents=True, exist_ok=True)
            self._update_progress(task_id, 20, "正在识别对白并对齐字幕")
            subtitle_alignment = align_export_subtitles(
                snapshot,
                work_dir,
                cancel_requested=lambda: self._cancel_requested(task_id),
            )
            ffmpeg_args, manifest = build_export_ffmpeg_args_from_snapshot(
                snapshot,
                output_path,
                subtitle_alignment=subtitle_alignment,
            )
            self._update_progress(task_id, 30, "FFmpeg 正在合成成片")
            result = run_cancellable_media_process(
                ffmpeg_args,
                cancel_requested=lambda: self._cancel_requested(task_id),
                timeout_sec=settings.provider_timeout_sec,
            )
            if result.returncode != 0:
                raise TaskExecutionError(
                    "ffmpeg_export_failed",
                    result.output_tail or "FFmpeg export failed",
                    submission_state=SubmissionState.NOT_SUBMITTED,
                )
            if not output_path.is_file() or output_path.stat().st_size == 0:
                raise TaskExecutionError(
                    "ffmpeg_export_missing_output",
                    "FFmpeg did not create the export file",
                    submission_state=SubmissionState.NOT_SUBMITTED,
                )
            if self._cancel_requested(task_id):
                return
            self._update_progress(task_id, 85, "正在保存导出文件")
            output_uri = get_media_store().put_file(
                str(snapshot.get("project_id") or self._project_id(task_id)),
                output_path,
                namespace="exports",
                filename=f"{task_id}.mp4",
            )
            if self._cancel_requested(task_id):
                get_media_store().delete(output_uri)
                return
            with SessionLocal() as db:
                task = db.get(GenerationTask, task_id)
                if task is None:
                    get_media_store().delete(output_uri)
                    return
                if task.cancel_requested_at is not None or task.status == TaskStatus.CANCELLING.value:
                    get_media_store().delete(output_uri)
                    return
                try:
                    persist_export_task_result(
                        db,
                        task,
                        output_uri=output_uri,
                        ffmpeg_args=ffmpeg_args,
                        manifest=manifest,
                    )
                except Exception:
                    db.rollback()
                    get_media_store().delete(output_uri)
                    raise
        except MediaProcessCancelled:
            return
        except TaskExecutionError as exc:
            self._record_failure(task_id, exc)
            raise
        except Exception as exc:
            normalized = TaskExecutionError(
                "ffmpeg_export_failed",
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
                "export",
                status="cancelled",
                task_id=task.id,
                error_code="export_task_cancelled",
                error_message="导出任务已取消",
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
    def _project_id(task_id: str) -> str:
        with SessionLocal() as db:
            task = db.get(GenerationTask, task_id)
            if task is None:
                raise ValueError("Export task no longer exists")
            return task.project_id

    @staticmethod
    def _record_failure(task_id: str, exc: TaskExecutionError) -> None:
        with SessionLocal() as db:
            task = db.get(GenerationTask, task_id)
            if task is None:
                return
            mark_stage_failed(
                db,
                task.project_id,
                "export",
                summary=str(exc),
                task_id=task.id,
                error_code=exc.code,
            )
            db.commit()


def _work_dir(task_id: str) -> Path:
    safe_task_id = "".join(character for character in task_id if character.isalnum() or character in "-_")
    if not safe_task_id:
        raise ValueError("Invalid export task ID")
    return Path(tempfile.gettempdir()) / "verbascene-export-tasks" / safe_task_id
