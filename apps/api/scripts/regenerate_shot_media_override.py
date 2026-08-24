#!/usr/bin/env python3
"""Generate a targeted frame or video candidate with an exact prompt override.

This is an operational fallback for shots whose UI regeneration control is not
available yet. It deliberately bypasses the global prompt compiler without
changing its production behavior.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from sqlalchemy import func, select

API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from app.agents.video_generation import shot_video_params  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import Asset, AssetCandidate, Shot, ShotFrameImage, ShotFramePrompt  # noqa: E402
from app.providers.defaults import provider_registry  # noqa: E402
from app.providers.types import ProviderRequest, ProviderType  # noqa: E402
from app.services.asset_service import (  # noqa: E402
    _create_generation_task,
    _finish_candidate_generation_task,
    _finish_generation_task,
    _generate_image_asset,
    _persist_provider_video_candidate,
)
from app.services.provider_config_service import reload_runtime_provider_configs  # noqa: E402
from app.services.task_service import mark_task_failed, mark_task_running  # noqa: E402
from app.services.workflow_state_service import mark_stage_ready, mark_stage_running  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    frame_parser = subparsers.add_parser("frame")
    frame_parser.add_argument("shot_id")
    frame_parser.add_argument("--frame-type", default="first", choices=("first", "key", "last", "action"))
    frame_parser.add_argument("--prompt", required=True)
    frame_parser.add_argument("--negative-prompt", default="")

    batch_parser = subparsers.add_parser("video-batch")
    batch_parser.add_argument("config_path")

    args = parser.parse_args()
    with SessionLocal() as db:
        reload_runtime_provider_configs(db)

    if args.command == "frame":
        generate_frame_override(
            args.shot_id,
            frame_type=args.frame_type,
            prompt=args.prompt,
            negative_prompt=args.negative_prompt or None,
        )
    else:
        run_video_batch(Path(args.config_path))


def generate_frame_override(
    shot_id: str,
    *,
    frame_type: str,
    prompt: str,
    negative_prompt: str | None,
) -> None:
    with SessionLocal() as db:
        shot = db.get(Shot, shot_id)
        if shot is None:
            raise ValueError(f"Shot not found: {shot_id}")

        prompt_version = int(
            db.scalar(
                select(func.coalesce(func.max(ShotFramePrompt.version), 0))
                .where(ShotFramePrompt.project_id == shot.project_id)
                .where(ShotFramePrompt.shot_id == shot.id)
                .where(ShotFramePrompt.frame_type == frame_type)
            )
            or 0
        ) + 1
        frame_prompt = ShotFramePrompt(
            project_id=shot.project_id,
            shot_id=shot.id,
            frame_type=frame_type,
            version=prompt_version,
            prompt=prompt,
            description="人工定向重写，用于视频首帧纠偏",
            layout="16:9 横屏，固定机位，主体分离",
            source="manual_override",
            provider=settings.image_provider,
            model=settings.image_model,
            raw_response={"override": True, "reason": "bad_video_first_frame"},
            status="approved",
        )
        db.add(frame_prompt)
        db.flush()

        image_provider = provider_registry.get(ProviderType.IMAGE, settings.image_provider)
        task = _create_generation_task(
            db,
            project_id=shot.project_id,
            task_type="manual_shot_frame_override_generation",
            input_payload={
                "shot_id": shot.id,
                "shot_no": shot.shot_no,
                "frame_type": frame_type,
                "frame_prompt_id": frame_prompt.id,
                "exact_prompt_override": True,
            },
            provider=image_provider.name,
            model=image_provider.model,
        )
        mark_task_running(db, task, f"正在定向重生成镜头 {shot.shot_no} 的 {frame_type} 帧", 20)
        task_id = task.id
        frame_prompt_id = frame_prompt.id
        project_id = shot.project_id
        db.commit()

        try:
            asset = _generate_image_asset(
                db,
                project_id=project_id,
                task_id=task_id,
                entity_type="shot_frame",
                entity_id=frame_prompt_id,
                asset_role=f"shot_frame_{frame_type}",
                prompt=prompt,
                negative_prompt=negative_prompt,
                asset_resolution=None,
            )
            image_version = int(
                db.scalar(
                    select(func.coalesce(func.max(ShotFrameImage.version), 0))
                    .where(ShotFrameImage.project_id == project_id)
                    .where(ShotFrameImage.shot_id == shot_id)
                    .where(ShotFrameImage.frame_type == frame_type)
                )
                or 0
            ) + 1
            frame_image = ShotFrameImage(
                project_id=project_id,
                shot_id=shot_id,
                frame_prompt_id=frame_prompt_id,
                asset_id=asset.id,
                frame_type=frame_type,
                image_type="generated",
                version=image_version,
                provider=asset.provider,
                model=asset.model,
                prompt=prompt,
                status="ready_for_review",
            )
            db.add(frame_image)
            db.flush()
            _finish_generation_task(db, task, [asset])
            _emit(
                "frame_override_succeeded",
                shot_id=shot_id,
                frame_type=frame_type,
                frame_prompt_id=frame_prompt_id,
                frame_image_id=frame_image.id,
                asset_id=asset.id,
                uri=asset.uri,
            )
        except Exception as exc:
            mark_task_failed(db, task, code="manual_frame_override_failed", message=str(exc))
            db.commit()
            raise


def run_video_batch(config_path: Path) -> None:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    project_id = str(config["project_id"])
    wait_for_shot_nos = [int(value) for value in config.get("wait_for_shot_nos", [])]
    poll_seconds = max(5, int(config.get("poll_seconds", 15)))

    while wait_for_shot_nos and not _shots_have_video_candidates(project_id, wait_for_shot_nos):
        _emit("waiting_for_existing_video_batch", shot_nos=wait_for_shot_nos)
        time.sleep(poll_seconds)

    for job in config["jobs"]:
        generate_video_override(
            str(job["shot_id"]),
            prompt=str(job["prompt"]),
            negative_prompt=str(job.get("negative_prompt") or "") or None,
            image_asset_id=str(job.get("image_asset_id") or "") or None,
        )


def generate_video_override(
    shot_id: str,
    *,
    prompt: str,
    negative_prompt: str | None,
    image_asset_id: str | None,
) -> None:
    with SessionLocal() as db:
        shot = db.get(Shot, shot_id)
        if shot is None:
            raise ValueError(f"Shot not found: {shot_id}")

        pending = db.scalar(
            select(AssetCandidate.id)
            .where(AssetCandidate.project_id == shot.project_id)
            .where(AssetCandidate.asset_type == "video")
            .where(AssetCandidate.entity_type == "shot")
            .where(AssetCandidate.entity_id == shot.id)
            .where(AssetCandidate.status == "pending_review")
        )
        if pending:
            raise ValueError(f"Shot {shot.shot_no} already has a pending video candidate: {pending}")

        image_asset = db.get(Asset, image_asset_id) if image_asset_id else _latest_first_frame_asset(db, shot)
        if image_asset is None:
            raise ValueError(f"Shot {shot.shot_no} has no usable first-frame asset")

        shot.video_prompt = prompt
        db.add(shot)
        video_provider = provider_registry.get(ProviderType.VIDEO, settings.video_provider)
        task = _create_generation_task(
            db,
            project_id=shot.project_id,
            task_type="manual_shot_video_prompt_override_generation",
            input_payload={
                "shot_id": shot.id,
                "shot_no": shot.shot_no,
                "image_asset_id": image_asset.id,
                "video_prompt": prompt,
                "exact_prompt_override": True,
            },
            provider=video_provider.name,
            model=video_provider.model,
        )
        mark_stage_running(db, shot.project_id, "videos", task_id=task.id)
        mark_task_running(db, task, f"正在使用重写 Prompt 生成镜头 {shot.shot_no}", 20)
        task_id = task.id
        project_id = shot.project_id
        shot_no = shot.shot_no
        params = shot_video_params(
            shot,
            fps=settings.wan_i2v_fps,
            max_frames=settings.wan_i2v_frames,
            default_steps=settings.wan_i2v_steps,
            default_guidance=settings.wan_i2v_guidance,
        )
        db.commit()

        _emit(
            "video_override_started",
            shot_no=shot_no,
            shot_id=shot_id,
            image_asset_id=image_asset.id,
        )
        try:
            response = video_provider.submit(
                ProviderRequest(
                    project_id=project_id,
                    task_id=f"{task_id}:exact-video-prompt:shot:{shot_id}",
                    model=video_provider.model,
                    prompt=prompt,
                    negative_prompt=negative_prompt,
                    references=[image_asset.uri],
                    params={**params, "reference_mode": "exact_first_frame_override"},
                    metadata={
                        "entity_type": "shot",
                        "entity_id": shot_id,
                        "image_asset_id": image_asset.id,
                        "exact_prompt_override": True,
                    },
                )
            )
            candidate = _persist_provider_video_candidate(
                db,
                project_id=project_id,
                entity_type="shot",
                entity_id=shot_id,
                provider_name=video_provider.name,
                model=video_provider.model,
                prompt=prompt,
                negative_prompt=negative_prompt,
                response=response,
                source_task_id=task_id,
                request_metadata={
                    "image_asset_id": image_asset.id,
                    "exact_prompt_override": True,
                    "prompt_source": "manual_shot_video_override",
                },
            )
            mark_stage_ready(
                db,
                project_id,
                "videos",
                task_id=task_id,
                summary=f"镜头 {shot_no} 重写 Prompt 视频候选已生成",
                metadata={"candidate_count": 1, "kind": "manual_video_prompt_override", "shot_id": shot_id},
            )
            _finish_candidate_generation_task(db, task, [candidate])
            _emit(
                "video_override_succeeded",
                shot_no=shot_no,
                shot_id=shot_id,
                candidate_id=candidate.id,
                uri=candidate.uri,
            )
        except Exception as exc:
            mark_task_failed(db, task, code="manual_video_override_failed", message=str(exc))
            db.commit()
            raise


def _latest_first_frame_asset(db, shot: Shot) -> Asset | None:
    frame_images = list(
        db.scalars(
            select(ShotFrameImage)
            .where(ShotFrameImage.project_id == shot.project_id)
            .where(ShotFrameImage.shot_id == shot.id)
            .where(ShotFrameImage.frame_type == "first")
            .where(ShotFrameImage.status.in_(["ready_for_review", "approved"]))
            .order_by(ShotFrameImage.version.desc(), ShotFrameImage.created_at.desc())
        ).all()
    )
    for frame_image in frame_images:
        if not frame_image.asset_id:
            continue
        asset = db.get(Asset, frame_image.asset_id)
        if asset and asset.is_selected and asset.status in {"ready_for_review", "approved"}:
            return asset
    return None


def _shots_have_video_candidates(project_id: str, shot_nos: list[int]) -> bool:
    with SessionLocal() as db:
        shots = list(
            db.scalars(
                select(Shot)
                .where(Shot.project_id == project_id)
                .where(Shot.shot_no.in_(shot_nos))
                .where(Shot.is_current.is_(True))
            ).all()
        )
        if len(shots) != len(set(shot_nos)):
            return False
        for shot in shots:
            candidate_available = db.scalar(
                select(AssetCandidate.id)
                .where(AssetCandidate.project_id == project_id)
                .where(AssetCandidate.asset_type == "video")
                .where(AssetCandidate.entity_type == "shot")
                .where(AssetCandidate.entity_id == shot.id)
                .where(AssetCandidate.status.in_(["pending_review", "promoted"]))
            )
            asset_available = db.scalar(
                select(Asset.id)
                .where(Asset.project_id == project_id)
                .where(Asset.asset_type == "video")
                .where(Asset.entity_type == "shot")
                .where(Asset.entity_id == shot.id)
            )
            if not candidate_available and not asset_available:
                return False
        return True


def _emit(event: str, **payload: object) -> None:
    print(json.dumps({"event": event, **payload}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
