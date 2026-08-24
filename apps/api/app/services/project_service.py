from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException, status
from sqlalchemy import Select, delete, func, select
from sqlalchemy.orm import Session, selectinload

from app.agents.style import resolve_visual_style
from app.core.config import settings
from app.models import (
    Asset,
    AssetCandidate,
    Chapter,
    Character,
    Dialogue,
    Export,
    GenerationTask,
    Project,
    ProjectStageRun,
    Prop,
    QualityCheck,
    Scene,
    Script,
    Shot,
    ShotFrameImage,
    ShotFramePrompt,
)
from app.platform.tasks.types import ACTIVE_TASK_STATUSES
from app.schemas.project import (
    DEFAULT_CREATIVE_SETTINGS,
    ChapterUpsert,
    ProjectCreate,
    ProjectDeleteRequest,
    ProjectUpdate,
)
from app.services.workflow_state_service import mark_downstream_stages_pending
from app.services.visual_style_service import rebase_stored_visual_prompts


PROJECT_RECORD_MODELS = {
    "chapters": Chapter,
    "scripts": Script,
    "characters": Character,
    "scenes": Scene,
    "props": Prop,
    "shots": Shot,
    "dialogues": Dialogue,
    "shot_frame_prompts": ShotFramePrompt,
    "shot_frame_images": ShotFrameImage,
    "assets": Asset,
    "asset_candidates": AssetCandidate,
    "generation_tasks": GenerationTask,
    "project_stage_runs": ProjectStageRun,
    "quality_checks": QualityCheck,
    "exports": Export,
}


def create_project(db: Session, payload: ProjectCreate) -> Project:
    creative_settings = {
        **DEFAULT_CREATIVE_SETTINGS,
        **payload.creative_settings.model_dump(),
    }
    visual_style = resolve_visual_style(payload.style, creative_settings)
    creative_settings["animation_style"] = visual_style
    project = Project(
        title=payload.title,
        style=visual_style,
        target_duration_sec=payload.target_duration_sec,
        resolution=payload.resolution,
        aspect_ratio=payload.aspect_ratio,
        creative_settings=creative_settings,
    )

    db.add(project)
    chapter = Chapter(
        project=project,
        input_mode=payload.input_mode,
        outline=(payload.outline or "").strip(),
        source_text=(payload.source_text or "").strip(),
        source_word_count=len((payload.source_text or "").strip()),
    )
    db.add(chapter)
    db.commit()
    db.refresh(project)
    return get_project(db, project.id)


def list_projects(db: Session, limit: int = 50, offset: int = 0) -> list[Project]:
    statement = (
        select(Project)
        .order_by(Project.updated_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(db.scalars(statement).all())


def list_projects_page(db: Session, limit: int = 50, offset: int = 0) -> tuple[list[Project], int]:
    statement = (
        select(Project)
        .order_by(Project.updated_at.desc())
        .limit(limit)
        .offset(offset)
    )
    total = db.scalar(select(func.count(Project.id))) or 0
    projects = list(db.scalars(statement).all())
    return projects, total


def get_project(db: Session, project_id: str) -> Project | None:
    statement: Select[tuple[Project]] = (
        select(Project)
        .options(selectinload(Project.chapters))
        .where(Project.id == project_id)
    )
    return db.scalar(statement)


def get_project_deletion_preview(db: Session, project_id: str) -> dict:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    record_counts = {
        label: db.scalar(select(func.count(model.id)).where(model.project_id == project_id)) or 0
        for label, model in PROJECT_RECORD_MODELS.items()
    }
    active_task_count = db.scalar(
        select(func.count(GenerationTask.id))
        .where(GenerationTask.project_id == project_id)
        .where(GenerationTask.status.in_(tuple(ACTIVE_TASK_STATUSES)))
    ) or 0
    active_stage_run_count = db.scalar(
        select(func.count(ProjectStageRun.id))
        .where(ProjectStageRun.project_id == project_id)
        .where(ProjectStageRun.status == "running")
    ) or 0
    storage_summary = _project_storage_summary(project_id)
    blocker_reasons: list[str] = []
    if active_task_count:
        blocker_reasons.append(f"仍有 {active_task_count} 个生成任务处于活动状态")
    if active_stage_run_count:
        blocker_reasons.append(f"仍有 {active_stage_run_count} 个阶段运行未结束")

    return {
        "project_id": project.id,
        "title": project.title,
        "record_counts": record_counts,
        "total_related_records": sum(record_counts.values()),
        "active_task_count": active_task_count,
        "active_stage_run_count": active_stage_run_count,
        **storage_summary,
        "can_delete": not blocker_reasons,
        "blocker_reasons": blocker_reasons,
    }


def delete_project(db: Session, project_id: str, payload: ProjectDeleteRequest) -> dict:
    preview = get_project_deletion_preview(db, project_id)
    if payload.confirmation_title.strip() != preview["title"].strip():
        raise HTTPException(
            status_code=422,
            detail={
                "code": "project_title_confirmation_mismatch",
                "message": "项目名称不匹配，未执行删除。",
            },
        )
    if not preview["can_delete"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "project_delete_blocked",
                "message": "项目仍有运行中的任务，未执行删除。",
                "blocker_reasons": preview["blocker_reasons"],
            },
        )

    moved_storage = _move_project_storage_to_trash(project_id)
    try:
        result = db.execute(
            delete(Project)
            .where(Project.id == project_id)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
        db.commit()
    except Exception:
        db.rollback()
        _restore_project_storage_from_trash(moved_storage)
        raise

    return {
        "project_id": project_id,
        "title": preview["title"],
        "deleted_records": preview["total_related_records"] + 1,
        "storage_action": "moved_to_trash" if moved_storage else "not_found",
        "storage_trash_path": moved_storage[2] if moved_storage else None,
    }


def update_project(db: Session, project: Project, payload: ProjectUpdate) -> Project:
    updates = payload.model_dump(exclude_unset=True)
    if payload.creative_settings is not None and "creative_settings" in updates:
        updates["creative_settings"] = payload.creative_settings.model_dump(exclude_unset=True)
    previous_style = resolve_visual_style(project.style, project.creative_settings)
    if "aspect_ratio" in updates or "resolution" in updates:
        next_aspect_ratio = updates.get("aspect_ratio", project.aspect_ratio)
        next_resolution = updates.get("resolution", project.resolution)
        expected_resolution = {"16:9": "854x480", "9:16": "480x854"}[next_aspect_ratio]
        if next_resolution != expected_resolution:
            raise HTTPException(
                status_code=422,
                detail=f"{next_aspect_ratio} 必须使用 {expected_resolution}",
            )
    requested_settings: dict = {}
    if "creative_settings" in updates:
        requested_settings = updates["creative_settings"]
        if hasattr(requested_settings, "model_dump"):
            requested_settings = requested_settings.model_dump()
    if "style" in updates or "creative_settings" in updates:
        requested_style = (
            updates.get("style")
            if "style" in updates
            else requested_settings.get("animation_style", previous_style)
        )
        visual_style = resolve_visual_style(str(requested_style or ""))
        updates["style"] = visual_style
        updates["creative_settings"] = {
            **DEFAULT_CREATIVE_SETTINGS,
            **(project.creative_settings or {}),
            **(requested_settings or {}),
            "animation_style": visual_style,
        }
    next_style = str(updates.get("style") or previous_style)
    style_changed = next_style != previous_style
    for field, value in updates.items():
        setattr(project, field, value)

    db.add(project)
    if any(
        field in updates
        for field in ("style", "target_duration_sec", "aspect_ratio", "resolution", "creative_settings")
    ):
        mark_downstream_stages_pending(db, project.id, "input", summary="项目规格已更新，需要重新生成后续内容")
    if style_changed:
        rebase_stored_visual_prompts(
            db,
            project.id,
            previous_style=previous_style,
        )
    db.commit()
    db.refresh(project)
    return get_project(db, project.id)


def upsert_project_chapter(db: Session, project: Project, payload: ChapterUpsert) -> Project:
    chapter = project.chapters[0] if project.chapters else Chapter(project=project)
    chapter.input_mode = payload.input_mode
    chapter.outline = payload.outline
    chapter.source_text = payload.source_text
    chapter.source_word_count = len(payload.source_text)
    chapter.status = "draft"
    db.add(chapter)
    db.add(project)
    db.flush()
    mark_downstream_stages_pending(db, project.id, "input", summary="项目输入已更新，需要重新生成后续内容")
    db.commit()
    db.refresh(project)
    return get_project(db, project.id)


def _project_storage_summary(project_id: str) -> dict[str, int | bool]:
    project_dir = _project_storage_dir(project_id)
    if not project_dir.is_dir():
        return {
            "storage_present": False,
            "storage_file_count": 0,
            "storage_bytes": 0,
        }

    file_count = 0
    storage_bytes = 0
    for path in project_dir.rglob("*"):
        try:
            if path.is_file():
                file_count += 1
                storage_bytes += path.stat().st_size
        except OSError:
            continue
    return {
        "storage_present": True,
        "storage_file_count": file_count,
        "storage_bytes": storage_bytes,
    }


def _project_storage_dir(project_id: str) -> Path:
    storage_root = Path(settings.storage_root).resolve()
    projects_root = (storage_root / "projects").resolve()
    project_dir = (projects_root / project_id).resolve()
    if projects_root not in project_dir.parents:
        raise HTTPException(status_code=400, detail="Invalid project storage path")
    return project_dir


def _move_project_storage_to_trash(project_id: str) -> tuple[Path, Path, str] | None:
    project_dir = _project_storage_dir(project_id)
    if not project_dir.exists():
        return None
    if not project_dir.is_dir():
        raise HTTPException(status_code=500, detail="Project storage path is not a directory")

    storage_root = Path(settings.storage_root).resolve()
    trash_root = storage_root / ".trash" / "projects"
    trash_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    trash_dir = trash_root / f"{project_id}-{timestamp}"
    project_dir.replace(trash_dir)
    return project_dir, trash_dir, trash_dir.relative_to(storage_root).as_posix()


def _restore_project_storage_from_trash(moved_storage: tuple[Path, Path, str] | None) -> None:
    if moved_storage is None:
        return
    project_dir, trash_dir, _relative_path = moved_storage
    if trash_dir.exists() and not project_dir.exists():
        project_dir.parent.mkdir(parents=True, exist_ok=True)
        trash_dir.replace(project_dir)
