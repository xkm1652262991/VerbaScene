"""Asset selection, upload, deletion, derivation, and adoption side effects."""

from __future__ import annotations

import subprocess
import tempfile
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, status
from PIL import Image
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.agents.avd_asset_strategy import avd_asset_metadata
from app.core.config import settings
from app.models import Asset, AssetCandidate, GenerationTask, Shot
from app.platform.media import get_media_store
from app.platform.tasks.types import ACTIVE_TASK_STATUSES
from app.services.artifact_idempotency import artifact_completion_key
from app.services.asset_repository import (
    asset_source_context,
    ensure_asset_target_exists,
    get_project_or_404,
    infer_asset_role,
    list_current_assets,
    next_asset_version,
    next_candidate_version,
)
from app.services.media_generation_support import (
    create_generation_task,
    finish_candidate_generation_task,
)
from app.services.media_storage_service import (
    local_media_path,
    read_image_size,
    safe_image_extension,
    unlink_local_storage_file,
)
from app.services.task_service import mark_task_failed


def select_asset(db: Session, asset_id: str) -> Asset:
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    current_asset_ids = {item.id for item in list_current_assets(db, asset.project_id, asset_type=asset.asset_type)}
    if asset.id not in current_asset_ids:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot select an asset from an outdated project chain",
        )

    db.execute(
        update(Asset)
        .where(Asset.project_id == asset.project_id)
        .where(Asset.asset_type == asset.asset_type)
        .where(Asset.asset_role == asset.asset_role)
        .where(Asset.entity_type == asset.entity_type)
        .where(Asset.entity_id == asset.entity_id)
        .where(Asset.variant_key == asset.variant_key)
        .values(is_selected=False),
    )
    asset.is_selected = True
    db.add(asset)
    if asset.asset_type == "video":
        _sync_shot_duration_to_video_asset(db, asset)
    db.commit()
    db.refresh(asset)
    return asset


def extract_video_frame_candidate(
    db: Session,
    asset_id: str,
    *,
    time_sec: Decimal = Decimal("0"),
) -> tuple[AssetCandidate, GenerationTask]:
    source_asset = db.get(Asset, asset_id)
    if source_asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    if source_asset.asset_type != "video" or source_asset.entity_type != "shot" or not source_asset.entity_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only shot video assets can be used for frame extraction",
        )
    if time_sec < 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Frame time must be greater than or equal to zero",
        )
    if source_asset.duration_sec is not None and time_sec > source_asset.duration_sec:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Frame time exceeds the video duration",
        )

    source_path = local_media_path(source_asset.uri)
    candidate_id = str(uuid4())
    temporary_output = tempfile.NamedTemporaryFile(
        prefix=f"verbascene-frame-{candidate_id}-",
        suffix=".png",
        delete=False,
    )
    temporary_output.close()
    output_path = Path(temporary_output.name)
    task = create_generation_task(
        db,
        project_id=source_asset.project_id,
        task_type="video_frame_extraction",
        input_payload={
            "asset_id": source_asset.id,
            "shot_id": source_asset.entity_id,
            "time_sec": str(time_sec),
        },
        provider="local",
        model="ffmpeg",
    )
    args = [
        settings.ffmpeg_path,
        "-y",
        "-ss",
        format(time_sec.normalize(), "f"),
        "-i",
        str(source_path),
        "-frames:v",
        "1",
        "-update",
        "1",
        str(output_path),
    ]
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=settings.provider_timeout_sec,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        output_path.unlink(missing_ok=True)
        mark_task_failed(
            db,
            task,
            code="video_frame_extraction_failed",
            message=str(exc),
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Video frame extraction failed: {exc}",
        ) from exc
    if completed.returncode != 0 or not output_path.is_file() or output_path.stat().st_size == 0:
        output_path.unlink(missing_ok=True)
        message = (
            completed.stderr.strip()[-2000:]
            or completed.stdout.strip()[-2000:]
            or "FFmpeg did not create a frame"
        )
        mark_task_failed(
            db,
            task,
            code="video_frame_extraction_failed",
            message=message,
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Video frame extraction failed: {message}",
        )

    with Image.open(output_path) as image:
        width, height = image.size
    candidate_uri = get_media_store().put_file(
        source_asset.project_id,
        output_path,
        namespace="assets/extracted-frames",
        filename=f"{candidate_id}.png",
    )
    output_path.unlink(missing_ok=True)
    candidate = AssetCandidate(
        id=candidate_id,
        project_id=source_asset.project_id,
        source_task_id=task.id,
        completion_key=artifact_completion_key(
            task.id,
            artifact_kind="candidate",
            candidate_type="extracted_frame",
            asset_type="image",
            asset_role="shot_storyboard",
            entity_type="shot",
            entity_id=source_asset.entity_id,
        ),
        source_script_id=source_asset.source_script_id,
        source_shot_batch_id=source_asset.source_shot_batch_id,
        candidate_type="extracted_frame",
        asset_type="image",
        asset_role="shot_storyboard",
        entity_type="shot",
        entity_id=source_asset.entity_id,
        version=next_candidate_version(
            db,
            source_asset.project_id,
            "image",
            "shot",
            source_asset.entity_id,
            "shot_storyboard",
        ),
        uri=candidate_uri,
        mime_type="image/png",
        width=width,
        height=height,
        provider="local",
        model="ffmpeg",
        prompt=f"Extract frame at {time_sec}s from video asset {source_asset.id}",
        raw_response={
            "source_video_asset_id": source_asset.id,
            "time_sec": str(time_sec),
            "ffmpeg_args": args,
        },
        status="pending_review",
    )
    db.add(candidate)
    db.flush()
    finish_candidate_generation_task(db, task, [candidate])
    return candidate, task


def upload_image_asset(
    db: Session,
    project_id: str,
    *,
    content: bytes,
    filename: str,
    content_type: str | None,
    entity_type: str,
    entity_id: str,
    asset_role: str | None = None,
    variant_key: str = "base",
    prompt: str | None = None,
) -> Asset:
    get_project_or_404(db, project_id)
    if not content_type or not content_type.startswith("image/"):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Only image uploads are supported")
    if entity_type not in {"character", "scene", "prop", "shot"}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unsupported image asset target")
    if asset_role and asset_role.startswith("shot_frame_"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="独立首帧、关键帧和尾帧已停用；请上传或生成 shot_storyboard 分镜首帧",
        )
    ensure_asset_target_exists(db, project_id, entity_type, entity_id)
    resolved_asset_role = asset_role or infer_asset_role(asset_type="image", entity_type=entity_type, entity_id=entity_id)
    resolved_variant_key = variant_key.strip() or "base"
    if entity_type not in {"character", "scene", "prop"}:
        resolved_variant_key = ""

    extension = safe_image_extension(filename, content_type)
    stored_name = f"{entity_type}_{entity_id}_{uuid4().hex}{extension}"
    uri = get_media_store().put_bytes(
        project_id,
        content,
        namespace="uploads/images",
        filename=stored_name,
    )
    path = local_media_path(uri)
    width, height = read_image_size(path)
    db.execute(
        update(Asset)
        .where(Asset.project_id == project_id)
        .where(Asset.asset_type == "image")
        .where(Asset.asset_role == resolved_asset_role)
        .where(Asset.entity_type == entity_type)
        .where(Asset.entity_id == entity_id)
        .where(Asset.variant_key == (resolved_variant_key or None))
        .values(is_selected=False),
    )
    version = next_asset_version(
        db,
        project_id,
        "image",
        entity_type,
        entity_id,
        resolved_asset_role,
        resolved_variant_key or None,
    )
    source_script_id, source_shot_batch_id = asset_source_context(db, entity_type, entity_id)
    avd_usage = avd_asset_metadata(
        asset_type="image",
        asset_role=resolved_asset_role,
        entity_type=entity_type,
    )
    asset = Asset(
        project_id=project_id,
        asset_type="image",
        asset_role=resolved_asset_role,
        entity_type=entity_type,
        entity_id=entity_id,
        variant_key=resolved_variant_key or None,
        source_script_id=source_script_id,
        source_shot_batch_id=source_shot_batch_id,
        version=version,
        uri=uri,
        mime_type=content_type,
        width=width,
        height=height,
        provider="manual_upload",
        model=None,
        prompt=prompt,
        negative_prompt=None,
        raw_response={
            "source": "manual_upload",
            "original_filename": filename,
            "request_metadata": {
                "avd_asset_purpose": avd_usage["asset_purpose"],
                "avd_asset_usage": avd_usage,
            },
        },
        status="ready_for_review",
        is_selected=True,
    )
    db.add(asset)
    db.flush()
    db.commit()
    db.refresh(asset)
    return asset


def delete_asset(db: Session, asset_id: str) -> None:
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    active_tasks = list(
        db.scalars(
            select(GenerationTask)
            .where(GenerationTask.project_id == asset.project_id)
            .where(GenerationTask.status.in_(tuple(ACTIVE_TASK_STATUSES)))
        ).all()
    )
    blocking_task = next(
        (
            task
            for task in active_tasks
            if _payload_contains_identifier(task.input_payload, asset.id)
        ),
        None,
    )
    if blocking_task is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "asset_in_use_by_active_task",
                "message": "资产已被活动生成任务的输入快照引用",
                "task_id": blocking_task.id,
            },
        )

    project_id = asset.project_id
    asset_type = asset.asset_type
    entity_type = asset.entity_type
    entity_id = asset.entity_id
    asset_role = asset.asset_role
    was_selected = asset.is_selected
    original_uri = asset.uri
    _remove_asset_from_shot_references(db, project_id, asset.id)
    db.delete(asset)
    db.flush()

    replacement: Asset | None = None
    if was_selected and entity_type and entity_id:
        replacement = db.scalar(
            select(Asset)
            .where(Asset.project_id == project_id)
            .where(Asset.asset_type == asset_type)
            .where(Asset.asset_role == asset_role)
            .where(Asset.entity_type == entity_type)
            .where(Asset.entity_id == entity_id)
            .where(Asset.variant_key == asset.variant_key)
            .order_by(Asset.version.desc(), Asset.created_at.desc())
        )
        if replacement:
            replacement.is_selected = True
            db.add(replacement)
    if was_selected and asset_type == "video":
        if replacement is not None:
            _sync_shot_duration_to_video_asset(db, replacement)
        else:
            _restore_shot_planned_duration(db, entity_type, entity_id)
    db.commit()
    unlink_local_storage_file(original_uri)


def _remove_asset_from_shot_references(db: Session, project_id: str, asset_id: str) -> None:
    shots = list(db.scalars(select(Shot).where(Shot.project_id == project_id)).all())
    for shot in shots:
        card = dict(shot.shot_card or {})
        raw_reference_ids = card.get("reference_asset_ids")
        reference_ids = (
            [str(item) for item in raw_reference_ids if str(item)]
            if isinstance(raw_reference_ids, list)
            else []
        )
        remaining_reference_ids = [item for item in reference_ids if item != asset_id]
        removed_bound_reference = len(remaining_reference_ids) != len(reference_ids)
        removed_first_frame = card.get("video_reference_asset_id") == asset_id
        if not removed_bound_reference and not removed_first_frame:
            continue

        if removed_bound_reference:
            card["reference_asset_ids"] = remaining_reference_ids
            remaining_assets = (
                list(
                    db.scalars(
                        select(Asset)
                        .where(Asset.project_id == project_id)
                        .where(Asset.id.in_(remaining_reference_ids))
                    ).all()
                )
                if remaining_reference_ids
                else []
            )
            remaining_by_id = {item.id: item for item in remaining_assets}
            ordered_assets = [
                remaining_by_id[item_id]
                for item_id in remaining_reference_ids
                if item_id in remaining_by_id
            ]
            shot.character_ids = list(dict.fromkeys(
                item.entity_id
                for item in ordered_assets
                if item.entity_type == "character" and item.entity_id
            ))
            scene_asset = next((item for item in ordered_assets if item.entity_type == "scene"), None)
            shot.scene_id = scene_asset.entity_id if scene_asset else None
            shot.prop_ids = list(dict.fromkeys(
                item.entity_id
                for item in ordered_assets
                if item.entity_type == "prop" and item.entity_id
            ))
        if removed_first_frame:
            card.pop("video_reference_asset_id", None)
        card["prompt_stale"] = True
        card["stale_reason"] = "引用资产已删除，需要重新检查视频 Prompt"
        shot.shot_card = card
        shot.status = "ready_for_review"
        db.add(shot)


def _sync_shot_duration_to_video_asset(db: Session, asset: Asset) -> None:
    if asset.entity_type != "shot" or not asset.entity_id:
        return
    request_metadata = (
        asset.raw_response.get("request_metadata")
        if isinstance(asset.raw_response, dict)
        and isinstance(asset.raw_response.get("request_metadata"), dict)
        else {}
    )
    if asset.duration_sec is not None and asset.duration_sec > 0:
        apply_actual_video_duration_to_shot(
            db,
            entity_type=asset.entity_type,
            entity_id=asset.entity_id,
            duration_sec=asset.duration_sec,
            request_metadata=request_metadata,
        )
        return
    _restore_shot_planned_duration(db, asset.entity_type, asset.entity_id)


def _restore_shot_planned_duration(db: Session, entity_type: str | None, entity_id: str | None) -> None:
    if entity_type != "shot" or not entity_id:
        return
    shot = db.get(Shot, entity_id)
    if shot is None:
        return
    card = dict(shot.shot_card or {})
    segment_plan = (
        dict(card.get("segment_plan"))
        if isinstance(card.get("segment_plan"), dict)
        else {}
    )
    planned_value = segment_plan.get("planned_duration_sec")
    try:
        planned_duration = Decimal(str(planned_value)) if planned_value is not None else None
    except (ArithmeticError, ValueError):
        planned_duration = None
    shot.duration_sec = (
        planned_duration
        if planned_duration is not None and planned_duration.is_finite() and planned_duration > 0
        else None
    )
    segment_plan.pop("actual_duration_sec", None)
    card["segment_plan"] = segment_plan
    shot.shot_card = card
    db.add(shot)


def _payload_contains_identifier(value: object, identifier: str) -> bool:
    if isinstance(value, dict):
        return any(_payload_contains_identifier(item, identifier) for item in value.values())
    if isinstance(value, list):
        return any(_payload_contains_identifier(item, identifier) for item in value)
    return isinstance(value, str) and value == identifier


def apply_actual_video_duration_to_shot(
    db: Session,
    *,
    entity_type: str,
    entity_id: str,
    duration_sec: Decimal | None,
    request_metadata: dict | None,
) -> None:
    if entity_type != "shot" or duration_sec is None or duration_sec <= 0:
        return
    shot = db.get(Shot, entity_id)
    if shot is None:
        return
    shot.duration_sec = duration_sec
    card = dict(shot.shot_card or {})
    segment_plan = (
        dict(card.get("segment_plan"))
        if isinstance(card.get("segment_plan"), dict)
        else {}
    )
    duration_request = (
        request_metadata.get("duration_request")
        if isinstance(request_metadata, dict)
        and isinstance(request_metadata.get("duration_request"), dict)
        else {}
    )
    segment_plan["duration_mode"] = str(
        duration_request.get("mode") or segment_plan.get("duration_mode") or "fixed"
    )
    segment_plan["actual_duration_sec"] = str(duration_sec)
    card["segment_plan"] = segment_plan
    shot.shot_card = card
    db.add(shot)
