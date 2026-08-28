import re
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from fastapi import HTTPException, status
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.agents import total_duration
from app.core.config import settings
from app.models import Asset, Dialogue, Export, GenerationTask, Project, Shot
from app.platform.media import get_media_store
from app.services.workflow_state_service import mark_stage_failed, mark_stage_ready, mark_stage_running
from app.services.task_service import mark_task_succeeded


@dataclass(frozen=True)
class SubtitleCue:
    start_sec: Decimal
    end_sec: Decimal
    text: str


@dataclass(frozen=True)
class SubtitleOverlay:
    start_sec: Decimal
    end_sec: Decimal
    text: str
    image_path: Path


def compose_project(
    db: Session,
    project_id: str,
    *,
    subtitle_mode: str = "none",
) -> tuple[Export, Asset]:
    if subtitle_mode not in {"none", "en", "bilingual"}:
        raise HTTPException(status_code=422, detail="Unsupported subtitle mode")
    project = get_project_or_404(db, project_id)
    mark_stage_running(db, project_id, "export")
    shots = _current_shots_for_export(db, project_id)
    video_assets = _require_selected_assets_for_entities(
        db,
        project_id,
        "video",
        "shot",
        [shot.id for shot in shots],
    )
    dialogues = _dialogues(db, project_id)
    duration_sec = total_duration(shots)
    version = _next_asset_version(
        db,
        project_id,
        "final_video",
        "project",
        project_id,
        "final_export",
    )
    provider_task_id = str(uuid5(NAMESPACE_URL, f"{project_id}:export:{version}:{subtitle_mode}"))
    output_path = _export_output_path(project_id, provider_task_id)
    video_has_audio = [_probe_has_audio(asset.uri) for asset in video_assets]
    subtitle_cues = (
        []
        if subtitle_mode == "none"
        else _dialogue_subtitle_cues(
            dialogues=dialogues,
            shots=shots,
            mode=subtitle_mode,
        )
    )
    ffmpeg_args = _build_ffmpeg_args(
        resolution=project.resolution,
        duration_sec=duration_sec,
        video_assets=video_assets,
        video_durations=[Decimal(str(shot.duration_sec or 0)) for shot in shots],
        video_has_audio=video_has_audio,
        subtitle_cues=subtitle_cues,
        output_path=output_path,
    )
    ffmpeg_command = shlex.join(ffmpeg_args)
    try:
        _run_ffmpeg(ffmpeg_args, output_path)
        output_uri = get_media_store().put_file(
            project_id,
            output_path,
            namespace="exports",
            filename=output_path.name,
        )
        stored_output_path = get_media_store().resolve_local_path(output_uri)
    except (HTTPException, OSError) as exc:
        detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
        mark_stage_failed(
            db,
            project_id,
            "export",
            summary=str(detail),
            error_code="ffmpeg_export_failed",
        )
        db.commit()
        shutil.rmtree(output_path.parent, ignore_errors=True)
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"FFmpeg export persistence failed: {exc}",
        ) from exc

    db.execute(
        update(Asset)
        .where(Asset.project_id == project_id)
        .where(Asset.asset_type == "final_video")
        .where(Asset.asset_role == "final_export")
        .where(Asset.entity_type == "project")
        .where(Asset.entity_id == project_id)
        .values(is_selected=False)
    )
    width, height = _parse_resolution(project.resolution)
    manifest = {
        "shots": [
            {
                "shot_id": shot.id,
                "shot_no": shot.shot_no,
                "duration_sec": str(shot.duration_sec or 0),
                "video_asset_id": asset.id,
                "video_asset_version": asset.version,
                "native_audio": has_audio,
                "audio_policy": "preserve_native" if has_audio else "inject_silence",
            }
            for shot, asset, has_audio in zip(
                shots,
                video_assets,
                video_has_audio,
                strict=True,
            )
        ],
        "subtitle_mode": subtitle_mode,
        "subtitle_cue_count": len(subtitle_cues),
        "dialogue_ids": [dialogue.id for dialogue in dialogues],
    }
    asset = Asset(
        project_id=project_id,
        asset_type="final_video",
        asset_role="final_export",
        entity_type="project",
        entity_id=project_id,
        version=version,
        uri=output_uri,
        mime_type="video/mp4",
        width=width,
        height=height,
        duration_sec=duration_sec,
        provider="local",
        model="ffmpeg",
        prompt=f"Concatenate selected video clips with native audio; subtitle_mode={subtitle_mode}",
        raw_response={
            "mode": "ffmpeg",
            "ffmpeg_command": ffmpeg_command,
            "manifest": manifest,
            "output_path": str(stored_output_path),
        },
        status="approved",
        is_selected=True,
    )
    db.add(asset)
    db.flush()
    export = Export(
        project_id=project_id,
        asset_id=asset.id,
        resolution=project.resolution,
        duration_sec=duration_sec,
        format="mp4",
        subtitle_mode=subtitle_mode,
        ffmpeg_command=ffmpeg_command,
        status="completed",
    )
    db.add(export)
    project.status = "exported"
    db.add(project)
    mark_stage_ready(
        db,
        project_id,
        "export",
        summary=f"成片版本 {version} 已导出",
        metadata={
            "export_id": export.id,
            "asset_id": asset.id,
            "version": version,
            "subtitle_mode": subtitle_mode,
        },
    )
    db.commit()
    db.refresh(asset)
    db.refresh(export)
    shutil.rmtree(output_path.parent, ignore_errors=True)
    return export, asset


def compile_export_task_input(
    db: Session,
    project_id: str,
    *,
    subtitle_mode: str,
) -> dict[str, Any]:
    """Freeze an export timeline without running ffprobe or FFmpeg."""
    if subtitle_mode not in {"none", "en", "bilingual"}:
        raise HTTPException(status_code=422, detail="Unsupported subtitle mode")
    project = get_project_or_404(db, project_id)
    shots = _current_shots_for_export(db, project_id)
    video_assets = _require_selected_assets_for_entities(
        db,
        project_id,
        "video",
        "shot",
        [shot.id for shot in shots],
    )
    dialogues = _dialogues(db, project_id)
    duration_sec = total_duration(shots)
    version = _next_asset_version(
        db,
        project_id,
        "final_video",
        "project",
        project_id,
        "final_export",
    )
    return {
        "project_id": project_id,
        "subtitle_mode": subtitle_mode,
        "resolution": project.resolution,
        "duration_sec": str(duration_sec),
        "version": version,
        "shots": [
            {
                "id": shot.id,
                "shot_no": shot.shot_no,
                "duration_sec": str(shot.duration_sec or 0),
                "shot_card": dict(shot.shot_card or {}),
            }
            for shot in shots
        ],
        "video_assets": [
            {
                "id": asset.id,
                "uri": asset.uri,
                "version": asset.version,
                "duration_sec": (
                    str(asset.duration_sec) if asset.duration_sec is not None else None
                ),
                "mime_type": asset.mime_type,
            }
            for asset in video_assets
        ],
        "dialogues": [
            {
                "id": dialogue.id,
                "shot_id": dialogue.shot_id,
                "beat_id": dialogue.beat_id,
                "sequence_order": dialogue.sequence_order,
                "start_time": (
                    str(dialogue.start_time) if dialogue.start_time is not None else None
                ),
                "end_time": (
                    str(dialogue.end_time) if dialogue.end_time is not None else None
                ),
                "text": dialogue.text,
                "translation_zh": dialogue.translation_zh,
            }
            for dialogue in dialogues
        ],
    }


def build_export_ffmpeg_args_from_snapshot(
    snapshot: dict[str, Any],
    output_path: Path,
) -> tuple[list[str], dict[str, Any]]:
    shots = [
        SimpleNamespace(
            id=str(item.get("id") or ""),
            shot_no=int(item.get("shot_no") or 0),
            duration_sec=Decimal(str(item.get("duration_sec") or 0)),
            shot_card=dict(item.get("shot_card") or {}),
        )
        for item in snapshot.get("shots") or []
        if isinstance(item, dict)
    ]
    asset_rows = [
        item
        for item in snapshot.get("video_assets") or []
        if isinstance(item, dict)
    ]
    video_assets = [SimpleNamespace(uri=str(item.get("uri") or "")) for item in asset_rows]
    dialogues = [
        SimpleNamespace(
            id=str(item.get("id") or ""),
            shot_id=item.get("shot_id"),
            beat_id=item.get("beat_id"),
            sequence_order=int(item.get("sequence_order") or 0),
            start_time=(
                Decimal(str(item.get("start_time")))
                if item.get("start_time") is not None
                else None
            ),
            end_time=(
                Decimal(str(item.get("end_time")))
                if item.get("end_time") is not None
                else None
            ),
            text=str(item.get("text") or ""),
            translation_zh=(
                str(item.get("translation_zh"))
                if item.get("translation_zh") is not None
                else None
            ),
        )
        for item in snapshot.get("dialogues") or []
        if isinstance(item, dict)
    ]
    if not shots or len(shots) != len(video_assets):
        raise ValueError("Frozen export timeline is incomplete")
    duration_sec = Decimal(str(snapshot.get("duration_sec") or 0))
    subtitle_mode = str(snapshot.get("subtitle_mode") or "none")
    video_has_audio = [_probe_has_audio(asset.uri) for asset in video_assets]
    subtitle_cues = (
        []
        if subtitle_mode == "none"
        else _dialogue_subtitle_cues(
            dialogues=dialogues,
            shots=shots,
            mode=subtitle_mode,
        )
    )
    args = _build_ffmpeg_args(
        resolution=str(snapshot.get("resolution") or "854x480"),
        duration_sec=duration_sec,
        video_assets=video_assets,
        video_durations=[Decimal(str(shot.duration_sec or 0)) for shot in shots],
        video_has_audio=video_has_audio,
        subtitle_cues=subtitle_cues,
        output_path=output_path,
    )
    manifest = {
        "shots": [
            {
                "shot_id": shot.id,
                "shot_no": shot.shot_no,
                "duration_sec": str(shot.duration_sec or 0),
                "video_asset_id": asset_row.get("id"),
                "video_asset_version": asset_row.get("version"),
                "native_audio": has_audio,
                "audio_policy": "preserve_native" if has_audio else "inject_silence",
            }
            for shot, asset_row, has_audio in zip(
                shots,
                asset_rows,
                video_has_audio,
                strict=True,
            )
        ],
        "subtitle_mode": subtitle_mode,
        "subtitle_cue_count": len(subtitle_cues),
        "dialogue_ids": [dialogue.id for dialogue in dialogues],
    }
    return args, manifest


def persist_export_task_result(
    db: Session,
    task: GenerationTask,
    *,
    output_uri: str,
    ffmpeg_args: list[str],
    manifest: dict[str, Any],
) -> tuple[Export, Asset]:
    snapshot = task.input_payload if isinstance(task.input_payload, dict) else {}
    resolution = str(snapshot.get("resolution") or "854x480")
    duration_sec = Decimal(str(snapshot.get("duration_sec") or 0))
    subtitle_mode = str(snapshot.get("subtitle_mode") or "none")
    version = int(snapshot.get("version") or 1)
    ffmpeg_command = shlex.join(ffmpeg_args)
    db.execute(
        update(Asset)
        .where(Asset.project_id == task.project_id)
        .where(Asset.asset_type == "final_video")
        .where(Asset.asset_role == "final_export")
        .where(Asset.entity_type == "project")
        .where(Asset.entity_id == task.project_id)
        .values(is_selected=False)
    )
    width, height = _parse_resolution(resolution)
    stored_path = get_media_store().resolve_local_path(output_uri)
    asset = Asset(
        project_id=task.project_id,
        asset_type="final_video",
        asset_role="final_export",
        entity_type="project",
        entity_id=task.project_id,
        source_task_id=task.id,
        version=version,
        uri=output_uri,
        mime_type="video/mp4",
        width=width,
        height=height,
        duration_sec=duration_sec,
        provider="local",
        model="ffmpeg",
        prompt=(
            "Concatenate selected video clips with native audio; "
            f"subtitle_mode={subtitle_mode}"
        ),
        raw_response={
            "mode": "ffmpeg",
            "ffmpeg_command": ffmpeg_command,
            "manifest": manifest,
            "output_path": str(stored_path),
        },
        status="approved",
        is_selected=True,
    )
    db.add(asset)
    db.flush()
    export = Export(
        project_id=task.project_id,
        asset_id=asset.id,
        resolution=resolution,
        duration_sec=duration_sec,
        format="mp4",
        subtitle_mode=subtitle_mode,
        ffmpeg_command=ffmpeg_command,
        status="completed",
    )
    db.add(export)
    project = db.get(Project, task.project_id)
    if project is not None:
        project.status = "exported"
        db.add(project)
    mark_stage_ready(
        db,
        task.project_id,
        "export",
        task_id=task.id,
        summary=f"成片版本 {version} 已导出",
        metadata={
            "export_id": export.id,
            "asset_id": asset.id,
            "version": version,
            "subtitle_mode": subtitle_mode,
        },
    )
    mark_task_succeeded(
        db,
        task,
        output_asset_ids=[asset.id],
        result_payload={
            "export_id": export.id,
            "asset_id": asset.id,
            "version": version,
            "subtitle_mode": subtitle_mode,
        },
        raw_response={
            **dict(task.raw_response or {}),
            "ffmpeg_command": ffmpeg_command,
            "manifest": manifest,
        },
    )
    db.add(task)
    db.commit()
    db.refresh(asset)
    db.refresh(export)
    db.refresh(task)
    return export, asset


def list_exports(db: Session, project_id: str) -> list[Export]:
    get_project_or_404(db, project_id)
    return list(
        db.scalars(
            select(Export)
            .where(Export.project_id == project_id)
            .order_by(Export.created_at.desc())
        ).all()
    )


def list_exports_page(
    db: Session,
    project_id: str,
    offset: int = 0,
    limit: int = 200,
) -> tuple[list[Export], int]:
    get_project_or_404(db, project_id)
    total = db.scalar(
        select(func.count(Export.id)).where(Export.project_id == project_id)
    ) or 0
    exports = list(
        db.scalars(
            select(Export)
            .where(Export.project_id == project_id)
            .order_by(Export.created_at.desc())
            .offset(offset)
            .limit(limit)
        ).all()
    )
    return exports, total


def get_project_or_404(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


def _current_shots_for_export(db: Session, project_id: str) -> list[Shot]:
    shots = list(
        db.scalars(
            select(Shot)
            .where(Shot.project_id == project_id)
            .where(Shot.is_current.is_(True))
            .order_by(Shot.shot_no)
        ).all()
    )
    if not shots:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Current video segments are required before export",
        )
    return shots


def _dialogues(db: Session, project_id: str) -> list[Dialogue]:
    return list(
        db.scalars(
            select(Dialogue)
            .where(Dialogue.project_id == project_id)
            .order_by(Dialogue.sequence_order, Dialogue.created_at)
        ).all()
    )


def _require_selected_assets_for_entities(
    db: Session,
    project_id: str,
    asset_type: str,
    entity_type: str,
    entity_ids: list[str],
) -> list[Asset]:
    assets = list(
        db.scalars(
            select(Asset)
            .where(Asset.project_id == project_id)
            .where(Asset.asset_type == asset_type)
            .where(Asset.entity_type == entity_type)
            .where(Asset.is_selected.is_(True))
            .order_by(Asset.created_at)
        ).all()
    )
    assets_by_entity = {asset.entity_id: asset for asset in assets}
    missing = [entity_id for entity_id in entity_ids if entity_id not in assets_by_entity]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "selected_video_versions_missing",
                "message": "每个片段都必须有当前采用的视频版本才能导出",
                "missing_shot_ids": missing,
            },
        )
    return [assets_by_entity[entity_id] for entity_id in entity_ids]


def _build_ffmpeg_args(
    *,
    resolution: str,
    duration_sec: Decimal,
    video_assets: list[Asset],
    video_durations: list[Decimal],
    video_has_audio: list[bool],
    subtitle_cues: list[SubtitleCue],
    output_path: Path,
) -> list[str]:
    width, height = _parse_resolution(resolution)
    inputs = [_media_input(asset.uri) for asset in video_assets]
    subtitle_overlays = _subtitle_overlays_from_cues(
        cues=subtitle_cues,
        output_path=output_path,
        width=width,
        height=height,
        duration_sec=duration_sec,
    )
    filter_complex = _build_filter_complex(
        width=width,
        height=height,
        duration_sec=duration_sec,
        video_count=len(video_assets),
        video_durations=video_durations,
        video_has_audio=video_has_audio,
        subtitle_overlays=subtitle_overlays,
    )
    args = [settings.ffmpeg_path, "-y"]
    for media_input in inputs:
        args.extend(["-i", media_input])
    for overlay in subtitle_overlays:
        args.extend(
            [
                "-loop",
                "1",
                "-t",
                _decimal_filter_value(duration_sec),
                "-i",
                str(overlay.image_path),
            ]
        )
    args.extend(
        [
            "-filter_complex",
            filter_complex,
            "-map",
            "[outv]",
            "-map",
            "[outa]",
            "-r",
            "24",
            "-s",
            f"{width}x{height}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )
    return args


def _build_filter_complex(
    *,
    width: int,
    height: int,
    duration_sec: Decimal,
    video_count: int,
    video_durations: list[Decimal],
    video_has_audio: list[bool],
    subtitle_overlays: list[SubtitleOverlay],
) -> str:
    filters: list[str] = []
    concat_inputs: list[str] = []
    for index in range(video_count):
        target = (
            video_durations[index]
            if index < len(video_durations) and video_durations[index] > 0
            else Decimal("3")
        )
        target_value = _decimal_filter_value(target)
        filters.append(
            f"[{index}:v]"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
            "setsar=1,"
            f"trim=duration={target_value},setpts=PTS-STARTPTS,"
            f"tpad=stop_mode=clone:stop_duration={target_value},"
            f"trim=duration={target_value},setpts=PTS-STARTPTS,"
            "fps=24,format=yuv420p"
            f"[v{index}]"
        )
        if index < len(video_has_audio) and video_has_audio[index]:
            filters.append(
                f"[{index}:a]"
                "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
                f"atrim=duration={target_value},asetpts=PTS-STARTPTS,"
                f"apad,atrim=duration={target_value}"
                f"[a{index}]"
            )
        else:
            filters.append(
                "anullsrc=channel_layout=stereo:sample_rate=48000,"
                f"atrim=duration={target_value},asetpts=N/SR/TB"
                f"[a{index}]"
            )
        concat_inputs.extend([f"[v{index}]", f"[a{index}]"])

    duration_value = _decimal_filter_value(duration_sec)
    filters.append(
        f"{''.join(concat_inputs)}concat=n={video_count}:v=1:a=1[vcat0][acat]"
    )
    filters.append(
        f"[vcat0]trim=duration={duration_value},setpts=PTS-STARTPTS[vcat]"
    )
    if subtitle_overlays:
        current_label = "vcat"
        subtitle_input_start = video_count
        for index, overlay in enumerate(subtitle_overlays):
            image_label = f"subimg{index}"
            next_label = f"vsub{index}"
            filters.append(
                f"[{subtitle_input_start + index}:v]format=rgba[{image_label}]"
            )
            filters.append(
                f"[{current_label}][{image_label}]"
                f"overlay=0:0:enable='between(t,{_decimal_filter_value(overlay.start_sec)},{_decimal_filter_value(overlay.end_sec)})':eof_action=pass"
                f"[{next_label}]"
            )
            current_label = next_label
        filters.append(f"[{current_label}]null[outv]")
    else:
        filters.append("[vcat]null[outv]")
    filters.append(
        f"[acat]atrim=duration={duration_value},asetpts=N/SR/TB[outa]"
    )
    return ";".join(filters)


def _dialogue_subtitle_cues(
    *,
    dialogues: list[Dialogue],
    shots: list[Shot],
    mode: str,
) -> list[SubtitleCue]:
    shot_offsets: dict[str, Decimal] = {}
    cursor = Decimal("0")
    for shot in shots:
        shot_offsets[shot.id] = cursor
        cursor += Decimal(str(shot.duration_sec or 0))

    grouped_by_beat: dict[tuple[str | None, str | None], list[Dialogue]] = {}
    for dialogue in dialogues:
        grouped_by_beat.setdefault((dialogue.shot_id, dialogue.beat_id), []).append(dialogue)

    cues: list[SubtitleCue] = []
    fallback_cursor = Decimal("0")
    shots_by_id = {shot.id: shot for shot in shots}
    for dialogue in dialogues:
        if dialogue.start_time is not None:
            start = Decimal(dialogue.start_time)
            end = (
                Decimal(dialogue.end_time)
                if dialogue.end_time is not None
                else start + Decimal("2")
            )
        else:
            shot = shots_by_id.get(dialogue.shot_id or "")
            if shot is not None:
                beat_start, beat_duration = _beat_window(shot, dialogue.beat_id)
                peers = grouped_by_beat.get((dialogue.shot_id, dialogue.beat_id), [dialogue])
                peer_index = peers.index(dialogue)
                slice_duration = beat_duration / max(1, len(peers))
                start = shot_offsets[shot.id] + beat_start + slice_duration * peer_index
                end = start + slice_duration
            else:
                start = fallback_cursor
                end = start + Decimal("2")
                fallback_cursor = end
        if end <= start:
            end = start + Decimal("0.8")
        text = dialogue.text
        if mode == "bilingual" and (dialogue.translation_zh or "").strip():
            text = f"{dialogue.text}\n{dialogue.translation_zh.strip()}"
        cues.append(SubtitleCue(start_sec=start, end_sec=end, text=text))
    return cues


def _beat_window(shot: Shot, beat_id: str | None) -> tuple[Decimal, Decimal]:
    card = shot.shot_card if isinstance(shot.shot_card, dict) else {}
    beats = card.get("beats")
    if not isinstance(beats, list) or not beats:
        return Decimal("0"), Decimal(str(shot.duration_sec or 2))
    ordered_beats = [beat for beat in beats if isinstance(beat, dict)]
    if not ordered_beats:
        return Decimal("0"), Decimal(str(shot.duration_sec or 2))
    segment_duration = Decimal(str(shot.duration_sec or 2))
    beat_duration = segment_duration / Decimal(len(ordered_beats))
    for index, beat in enumerate(ordered_beats):
        if beat_id and str(beat.get("beat_id") or "") == beat_id:
            return beat_duration * index, max(beat_duration, Decimal("0.8"))
    return Decimal("0"), Decimal(str(shot.duration_sec or 2))


def _subtitle_overlays_from_cues(
    *,
    cues: list[SubtitleCue],
    output_path: Path,
    width: int,
    height: int,
    duration_sec: Decimal,
) -> list[SubtitleOverlay]:
    overlays: list[SubtitleOverlay] = []
    overlay_dir = output_path.parent / "_subtitle_overlays" / output_path.stem
    for index, cue in enumerate(cues, start=1):
        if cue.start_sec >= duration_sec or cue.end_sec <= 0:
            continue
        start_sec = max(cue.start_sec, Decimal("0"))
        end_sec = min(cue.end_sec, duration_sec)
        image_path = overlay_dir / f"{index:03d}.png"
        _render_subtitle_image(cue.text, image_path, width, height)
        overlays.append(
            SubtitleOverlay(
                start_sec=start_sec,
                end_sec=end_sec,
                text=cue.text,
                image_path=image_path,
            )
        )
    return overlays


def _probe_has_audio(uri: str) -> bool:
    media_input = _media_input(uri)
    ffprobe = str(Path(settings.ffmpeg_path).with_name("ffprobe"))
    try:
        completed = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=index",
                "-of",
                "csv=p=0",
                media_input,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0 and bool(completed.stdout.strip())


@dataclass(frozen=True)
class _ParsedSubtitleCue:
    start_sec: Decimal
    end_sec: Decimal
    text: str


_SRT_TIMING_RE = re.compile(
    r"(?P<start>\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(?P<end>\d{2}:\d{2}:\d{2}[,.]\d{3})"
)


def _parse_srt_cues(content: str) -> list[_ParsedSubtitleCue]:
    cues: list[_ParsedSubtitleCue] = []
    for block in re.split(r"\n\s*\n", content.strip()):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timing_index = next(
            (index for index, line in enumerate(lines) if _SRT_TIMING_RE.search(line)),
            None,
        )
        if timing_index is None:
            continue
        match = _SRT_TIMING_RE.search(lines[timing_index])
        if match is None:
            continue
        text = "\n".join(lines[timing_index + 1 :]).strip()
        if text:
            cues.append(
                _ParsedSubtitleCue(
                    start_sec=_srt_timestamp_to_decimal(match.group("start")),
                    end_sec=_srt_timestamp_to_decimal(match.group("end")),
                    text=text,
                )
            )
    return cues


def _srt_timestamp_to_decimal(value: str) -> Decimal:
    hours_text, minutes_text, rest = value.replace(",", ".").split(":", 2)
    return Decimal(int(hours_text) * 3600 + int(minutes_text) * 60) + Decimal(rest)


def _render_subtitle_image(
    text: str,
    image_path: Path,
    width: int,
    height: int,
) -> None:
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    font_size = max(28, int(height * 0.054))
    font = _subtitle_font(font_size)
    stroke_width = max(2, int(font_size * 0.07))
    lines = _wrap_subtitle_text(
        text.replace("\r", ""),
        draw,
        font,
        int(width * 0.78),
        stroke_width,
    )
    metrics = [
        draw.textbbox((0, 0), line, font=font, stroke_width=stroke_width)
        for line in lines
    ]
    line_heights = [max(1, bottom - top) for _, top, _, bottom in metrics]
    line_gap = max(8, int(font_size * 0.20))
    text_height = sum(line_heights) + line_gap * max(0, len(lines) - 1)
    text_width = max((right - left for left, _, right, _ in metrics), default=1)
    padding_x = max(24, int(width * 0.024))
    padding_y = max(12, int(height * 0.017))
    box_width = min(width - 80, text_width + padding_x * 2)
    box_height = text_height + padding_y * 2
    box_left = (width - box_width) // 2
    box_top = height - int(height * 0.10) - box_height
    draw.rounded_rectangle(
        (box_left, box_top, box_left + box_width, box_top + box_height),
        radius=max(8, int(height * 0.012)),
        fill=(0, 0, 0, 142),
    )
    cursor_y = box_top + padding_y
    for line, line_height in zip(lines, line_heights, strict=False):
        bbox = draw.textbbox((0, 0), line, font=font, stroke_width=stroke_width)
        x = (width - (bbox[2] - bbox[0])) // 2
        draw.text(
            (x, cursor_y),
            line,
            font=font,
            fill=(255, 255, 255, 248),
            stroke_width=stroke_width,
            stroke_fill=(16, 18, 20, 230),
        )
        cursor_y += line_height + line_gap
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(image_path)


def _wrap_subtitle_text(
    text: str,
    draw: ImageDraw.ImageDraw,
    font: ImageFont.ImageFont,
    max_width: int,
    stroke_width: int,
) -> list[str]:
    result: list[str] = []
    for raw_line in text.splitlines():
        words = raw_line.strip().split(" ")
        current = ""
        for word in words:
            candidate = word if not current else f"{current} {word}"
            bbox = draw.textbbox(
                (0, 0),
                candidate,
                font=font,
                stroke_width=stroke_width,
            )
            if current and bbox[2] - bbox[0] > max_width:
                result.append(current)
                current = word
            else:
                current = candidate
        if current:
            result.append(current)
    return result or [text.strip()]


def _subtitle_font(font_size: int) -> ImageFont.ImageFont:
    for path in (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Supplemental/Songti.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
    ):
        font_path = Path(path)
        if font_path.is_file():
            try:
                return ImageFont.truetype(str(font_path), font_size)
            except OSError:
                continue
    return ImageFont.load_default()


def _run_ffmpeg(args: list[str], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=settings.provider_timeout_sec,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        output_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"FFmpeg export failed: {exc}",
        ) from exc
    if completed.returncode != 0:
        output_path.unlink(missing_ok=True)
        stderr = (
            completed.stderr.strip()[-2000:]
            or completed.stdout.strip()[-2000:]
            or "unknown ffmpeg error"
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"FFmpeg export failed: {stderr}",
        )
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="FFmpeg export failed: output file was not created",
        )


def _export_output_path(project_id: str, filename_stem: str) -> Path:
    work_dir = Path(
        tempfile.mkdtemp(prefix=f"verbascene-export-{project_id[:8]}-")
    )
    return work_dir / f"{filename_stem}.mp4"


def _media_input(uri: str) -> str:
    path = _local_storage_path(uri)
    if path is not None:
        if not path.exists():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Media input does not exist: {uri}",
            )
        return str(path)
    if uri.startswith("mock://"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Mock media cannot be exported: {uri}",
        )
    return uri


def _local_storage_path(uri: str) -> Path | None:
    base_url = settings.public_storage_base_url.rstrip("/") + "/"
    if not uri.startswith(base_url) and not uri.startswith("/storage/"):
        return None
    try:
        return get_media_store().resolve_local_path(uri)
    except (FileNotFoundError, ValueError):
        return None


def _parse_resolution(resolution: str) -> tuple[int, int]:
    try:
        width_text, height_text = resolution.lower().split("x", 1)
        width, height = int(width_text), int(height_text)
    except (TypeError, ValueError):
        return 1280, 720
    return (width, height) if width > 0 and height > 0 else (1280, 720)


def _decimal_filter_value(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _next_asset_version(
    db: Session,
    project_id: str,
    asset_type: str,
    entity_type: str,
    entity_id: str,
    asset_role: str | None = None,
) -> int:
    current = db.scalar(
        select(func.coalesce(func.max(Asset.version), 0))
        .where(Asset.project_id == project_id)
        .where(Asset.asset_type == asset_type)
        .where(Asset.asset_role == asset_role)
        .where(Asset.entity_type == entity_type)
        .where(Asset.entity_id == entity_id)
    )
    return int(current or 0) + 1
