from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Asset, ProjectStageRun, Shot
from app.services.stage_run_service import finish_latest_stage_run, start_stage_run


WORKSPACE_CATEGORY_BY_LEGACY_STAGE = {
    "input": "script",
    "script": "script",
    "entities": "assets",
    "images": "assets",
    "shots": "production",
    "videos": "production",
    "audio": "production",
    "export": "export",
}

STAGE_LABELS = {
    "script": "剧本",
    "assets": "资产库",
    "production": "视频制作",
    "export": "导出",
}


def workspace_category(stage_name: str) -> str:
    return WORKSPACE_CATEGORY_BY_LEGACY_STAGE.get(stage_name, stage_name)


def mark_stage_running(
    db: Session,
    project_id: str,
    stage_name: str,
    *,
    task_id: str | None = None,
) -> ProjectStageRun:
    category = workspace_category(stage_name)
    return start_stage_run(
        db,
        project_id,
        category,
        task_id=task_id,
        metadata={"legacy_stage": stage_name},
    )


def mark_stage_ready(
    db: Session,
    project_id: str,
    stage_name: str,
    *,
    task_id: str | None = None,
    summary: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> ProjectStageRun | None:
    return finish_latest_stage_run(
        db,
        project_id,
        workspace_category(stage_name),
        status="succeeded",
        task_id=task_id,
        output_payload={
            "summary": summary,
            "legacy_stage": stage_name,
            **(metadata or {}),
        },
    )


def mark_stage_approved(
    db: Session,
    project_id: str,
    stage_name: str,
    *,
    summary: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> ProjectStageRun:
    run = ProjectStageRun(
        project_id=project_id,
        stage=workspace_category(stage_name),
        action="adopt",
        status="succeeded",
        input_payload={},
        output_payload={
            "summary": summary,
            "legacy_stage": stage_name,
            **(metadata or {}),
        },
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
        run_metadata={"legacy_stage": stage_name},
    )
    db.add(run)
    db.flush()
    return run


def mark_stage_skipped(
    db: Session,
    project_id: str,
    stage_name: str,
    *,
    summary: str,
    metadata: dict[str, Any] | None = None,
) -> ProjectStageRun:
    run = ProjectStageRun(
        project_id=project_id,
        stage=workspace_category(stage_name),
        action="skip",
        status="succeeded",
        input_payload={},
        output_payload={
            "summary": summary,
            "legacy_stage": stage_name,
            **(metadata or {}),
        },
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
        run_metadata={"legacy_stage": stage_name},
    )
    db.add(run)
    db.flush()
    return run


def mark_stage_failed(
    db: Session,
    project_id: str,
    stage_name: str,
    *,
    summary: str,
    error_code: str | None = None,
    failure_reason: dict[str, Any] | None = None,
) -> ProjectStageRun | None:
    return finish_latest_stage_run(
        db,
        project_id,
        workspace_category(stage_name),
        status="failed",
        error_code=error_code,
        error_message=summary,
        failure_reason=failure_reason,
    )


def mark_downstream_stages_pending(
    db: Session,
    project_id: str,
    stage_name: str,
    *,
    summary: str | None = None,
) -> None:
    """Compatibility entrypoint that now marks output provenance as stale.

    It deliberately does not change page accessibility or delete/select assets.
    """

    if stage_name in {"input", "script", "entities", "shots", "images"}:
        shots = list(
            db.scalars(
                select(Shot)
                .where(Shot.project_id == project_id)
                .where(Shot.is_current.is_(True))
            ).all()
        )
        for shot in shots:
            shot_card = dict(shot.shot_card or {})
            shot_card["prompt_stale"] = True
            shot_card["stale_reason"] = summary or "上游内容已更新"
            shot.shot_card = shot_card
            db.add(shot)

    stale_asset_types: set[str] = set()
    if stage_name in {"input", "script", "entities", "shots", "images"}:
        stale_asset_types.add("video")
    if stage_name in {"input", "script", "entities", "shots", "images", "videos", "audio"}:
        stale_asset_types.add("final_video")
    if not stale_asset_types:
        return

    assets = list(
        db.scalars(
            select(Asset)
            .where(Asset.project_id == project_id)
            .where(Asset.asset_type.in_(stale_asset_types))
        ).all()
    )
    for asset in assets:
        raw_response = dict(asset.raw_response or {})
        raw_response["possibly_stale"] = True
        raw_response["stale_reason"] = summary or "上游内容已更新"
        asset.raw_response = raw_response
        db.add(asset)
