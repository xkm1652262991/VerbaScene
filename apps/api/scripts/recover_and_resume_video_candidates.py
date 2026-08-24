#!/usr/bin/env python3
"""Recover completed Wan jobs as candidates and resume only missing shots.

Example:
    .venv/bin/python scripts/recover_and_resume_video_candidates.py PROJECT_ID \
      --recover-job 1=JOB_ID --recover-job 2=JOB_ID --resume-missing

The command is idempotent: a recovered job URI or an existing pending video
candidate for a shot is never inserted/generated twice.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.request import Request, urlopen

from sqlalchemy import select

API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from app.core.config import settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import Asset, AssetCandidate, Shot  # noqa: E402
from app.services.asset_service import (
    _asset_source_context,
    _create_generation_task,
    _finish_candidate_generation_task,
    _next_candidate_version,
    _source_stage_run_id,
    generate_single_shot_video_candidate,
)  # noqa: E402
from app.services.provider_config_service import reload_runtime_provider_configs  # noqa: E402
from app.services.workflow_state_service import mark_stage_ready, mark_stage_running  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("project_id")
    parser.add_argument(
        "--recover-job",
        action="append",
        default=[],
        metavar="SHOT_NO=JOB_ID",
        help="Recover one succeeded Wan job for the numbered shot; repeatable.",
    )
    parser.add_argument(
        "--resume-missing",
        action="store_true",
        help="Generate one candidate for every approved shot still missing one.",
    )
    args = parser.parse_args()

    with SessionLocal() as db:
        reload_runtime_provider_configs(db)

    recovered = recover_jobs(args.project_id, [_parse_job_spec(value) for value in args.recover_job])
    _emit("recovery_complete", recovered_count=len(recovered), candidate_ids=[item.id for item in recovered])

    if args.resume_missing:
        resume_missing_shots(args.project_id)


def recover_jobs(project_id: str, job_specs: list[tuple[int, str]]) -> list[AssetCandidate]:
    if not job_specs:
        return []

    job_records = {
        shot_no: _fetch_succeeded_job(job_id)
        for shot_no, job_id in job_specs
    }
    recovered: list[AssetCandidate] = []

    with SessionLocal() as db:
        shots = {
            shot.shot_no: shot
            for shot in db.scalars(
                select(Shot)
                .where(Shot.project_id == project_id)
                .where(Shot.is_current.is_(True))
            ).all()
        }
        missing_shot_numbers = sorted(set(job_records) - set(shots))
        if missing_shot_numbers:
            raise ValueError(f"Unknown current shot numbers: {missing_shot_numbers}")

        pending_specs: list[tuple[Shot, dict, Path, str]] = []
        for shot_no, job in job_records.items():
            shot = shots[shot_no]
            job_id = str(job["id"])
            filename = f"{job_id}.mp4"
            local_path = (
                Path(settings.storage_root).resolve()
                / "projects"
                / project_id
                / "provider-results"
                / "wan2-i2v"
                / filename
            )
            if not local_path.is_file() or local_path.stat().st_size == 0:
                raise FileNotFoundError(f"Recovered Wan result is missing: {local_path}")

            uri = (
                f"{settings.public_storage_base_url.rstrip('/')}/projects/{project_id}"
                f"/provider-results/wan2-i2v/{filename}"
            )
            existing = db.scalar(
                select(AssetCandidate)
                .where(AssetCandidate.project_id == project_id)
                .where(AssetCandidate.asset_type == "video")
                .where(AssetCandidate.entity_type == "shot")
                .where(AssetCandidate.entity_id == shot.id)
                .where(AssetCandidate.uri == uri)
            )
            if existing is not None:
                continue

            prompt = str((job.get("request") or {}).get("prompt") or "")
            if shot.description and shot.description not in prompt:
                raise ValueError(
                    f"Wan job {job_id} does not match shot {shot_no}: {shot.description}"
                )
            pending_specs.append((shot, job, local_path, uri))

        if not pending_specs:
            return []

        provider = settings.video_provider
        model = settings.video_model
        task = _create_generation_task(
            db,
            project_id=project_id,
            task_type="shot_video_candidate_recovery",
            input_payload={
                "recovery_reason": "batch_process_exited_before_database_commit",
                "jobs": [
                    {"shot_no": shot.shot_no, "shot_id": shot.id, "wan_job_id": job["id"]}
                    for shot, job, _path, _uri in pending_specs
                ],
            },
            provider=provider,
            model=model,
        )
        mark_stage_running(db, project_id, "videos", task_id=task.id)
        db.flush()

        for shot, job, local_path, uri in pending_specs:
            request_payload = job.get("request") or {}
            source_script_id, source_shot_batch_id = _asset_source_context(db, "shot", shot.id)
            candidate = AssetCandidate(
                project_id=project_id,
                source_task_id=task.id,
                source_stage_run_id=_source_stage_run_id(db, project_id, task.id),
                source_script_id=source_script_id,
                source_shot_batch_id=source_shot_batch_id,
                candidate_type="generated",
                asset_type="video",
                asset_role="shot_video",
                entity_type="shot",
                entity_id=shot.id,
                version=_next_candidate_version(db, project_id, "video", "shot", shot.id, "shot_video"),
                uri=uri,
                mime_type="video/mp4",
                width=int(job.get("width") or 1280),
                height=int(job.get("height") or 720),
                duration_sec=Decimal(str(job.get("duration_sec") or shot.duration_sec or 0)),
                provider=provider,
                model=model,
                prompt=str(request_payload.get("prompt") or shot.video_prompt or ""),
                negative_prompt=str(request_payload.get("negative_prompt") or shot.negative_prompt or ""),
                raw_response={
                    "provider_response": {
                        "model": model,
                        "wan_job_id": job["id"],
                        "job": {
                            "id": job["id"],
                            "client_job_id": job.get("client_job_id"),
                            "status": job.get("status"),
                            "created_at": job.get("created_at"),
                            "finished_at": job.get("finished_at"),
                        },
                        "final_status": {
                            key: job.get(key)
                            for key in (
                                "status",
                                "seed",
                                "output_bytes",
                                "width",
                                "height",
                                "fps",
                                "frame_count",
                                "duration_sec",
                                "result_url",
                            )
                        },
                        "content_type": "video/mp4",
                    },
                    "request_metadata": {
                        "recovered": True,
                        "recovery_reason": "batch_process_exited_before_database_commit",
                        "recovered_at": datetime.now(timezone.utc).isoformat(),
                        "wan_job_id": job["id"],
                        "local_result_path": str(local_path),
                        "shot_id": shot.id,
                        "shot_no": shot.shot_no,
                    },
                },
                status="pending_review",
            )
            db.add(candidate)
            db.flush()
            recovered.append(candidate)

        mark_stage_ready(
            db,
            project_id,
            "videos",
            task_id=task.id,
            summary=f"已恢复 {len(recovered)} 个视频候选，等待补齐剩余镜头",
            metadata={
                "candidate_count": len(recovered),
                "kind": "recovered_shot_video_candidates",
            },
        )
        _finish_candidate_generation_task(db, task, recovered)
        return recovered


def resume_missing_shots(project_id: str) -> None:
    with SessionLocal() as db:
        shots = list(
            db.scalars(
                select(Shot)
                .where(Shot.project_id == project_id)
                .where(Shot.is_current.is_(True))
                .where(Shot.status == "approved")
                .order_by(Shot.shot_no)
            ).all()
        )

    for shot in shots:
        with SessionLocal() as db:
            already_available = db.scalar(
                select(AssetCandidate.id)
                .where(AssetCandidate.project_id == project_id)
                .where(AssetCandidate.asset_type == "video")
                .where(AssetCandidate.asset_role == "shot_video")
                .where(AssetCandidate.entity_type == "shot")
                .where(AssetCandidate.entity_id == shot.id)
                .where(AssetCandidate.status == "pending_review")
            ) or db.scalar(
                select(Asset.id)
                .where(Asset.project_id == project_id)
                .where(Asset.asset_type == "video")
                .where(Asset.asset_role == "shot_video")
                .where(Asset.entity_type == "shot")
                .where(Asset.entity_id == shot.id)
            )
            if already_available:
                _emit("shot_skipped", shot_no=shot.shot_no, reason="candidate_or_asset_exists")
                continue

            _emit("shot_generation_started", shot_no=shot.shot_no, shot_id=shot.id)
            candidate, task = generate_single_shot_video_candidate(db, shot.id)
            _emit(
                "shot_generation_succeeded",
                shot_no=shot.shot_no,
                candidate_id=candidate.id,
                task_id=task.id,
                uri=candidate.uri,
            )


def _fetch_succeeded_job(job_id: str) -> dict:
    if not settings.wan_i2v_api_key:
        raise ValueError("WAN_I2V_API_KEY is required to recover official Wan jobs")
    endpoint = f"{settings.wan_i2v_api_base_url.rstrip('/')}/v1/i2v/jobs/{job_id}"
    request = Request(endpoint, headers={"X-API-Key": settings.wan_i2v_api_key}, method="GET")
    with urlopen(request, timeout=settings.wan_i2v_api_timeout_sec) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if str(payload.get("status") or "").lower() != "succeeded":
        raise ValueError(f"Wan job is not succeeded: {job_id} ({payload.get('status')})")
    return payload


def _parse_job_spec(value: str) -> tuple[int, str]:
    shot_no_text, separator, job_id = value.partition("=")
    if not separator or not shot_no_text.isdigit() or not job_id.strip():
        raise argparse.ArgumentTypeError(f"Invalid --recover-job value: {value!r}")
    return int(shot_no_text), job_id.strip()


def _emit(event: str, **payload: object) -> None:
    print(json.dumps({"event": event, **payload}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
