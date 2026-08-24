from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fastapi import HTTPException, status

from app.models import Character, Dialogue, Project, Script, Shot
from app.schemas.dialogue import DialogueSaveRequest
from app.services.workflow_state_service import mark_downstream_stages_pending


def list_dialogues(db: Session, project_id: str) -> list[Dialogue]:
    script_id = _latest_script_id(db, project_id)
    if script_id is None:
        return []
    return list(
        db.scalars(
            select(Dialogue)
            .where(Dialogue.project_id == project_id)
            .where(Dialogue.script_id == script_id)
            .order_by(Dialogue.sequence_order, Dialogue.created_at)
        ).all()
    )


def list_dialogues_page(
    db: Session,
    project_id: str,
    *,
    offset: int = 0,
    limit: int = 500,
) -> tuple[list[Dialogue], int]:
    if db.get(Project, project_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    script_id = _latest_script_id(db, project_id)
    if script_id is None:
        return [], 0
    total = db.scalar(
        select(func.count(Dialogue.id))
        .where(Dialogue.project_id == project_id)
        .where(Dialogue.script_id == script_id)
    ) or 0
    rows = list(
        db.scalars(
            select(Dialogue)
            .where(Dialogue.project_id == project_id)
            .where(Dialogue.script_id == script_id)
            .order_by(Dialogue.sequence_order, Dialogue.created_at)
            .offset(offset)
            .limit(limit)
        ).all()
    )
    return rows, total


def synchronize_script_dialogues(db: Session, script: Script) -> list[Dialogue]:
    """Persist the script dialogue index and write stable row ids back to JSON."""

    scenes = script.scenes if isinstance(script.scenes, list) else []
    existing = list(
        db.scalars(
            select(Dialogue)
            .where(Dialogue.script_id == script.id)
            .order_by(Dialogue.sequence_order, Dialogue.created_at)
        ).all()
    )
    existing_by_id = {item.id: item for item in existing}
    existing_by_order = {item.sequence_order: item for item in existing}
    character_ids_by_name = {
        character.name: character.id
        for character in db.scalars(
            select(Character).where(Character.project_id == script.project_id)
        ).all()
    }

    kept_ids: set[str] = set()
    dialogue_index: list[dict] = []
    sequence_order = 0
    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        raw_dialogues = scene.get("dialogues")
        if not isinstance(raw_dialogues, list):
            raw_dialogues = []
        normalized_scene_dialogues: list[dict] = []
        for raw in raw_dialogues:
            if not isinstance(raw, dict):
                continue
            text = str(raw.get("text") or "").strip()
            if not text:
                continue
            requested_id = str(raw.get("id") or "").strip()
            row = existing_by_id.get(requested_id) or existing_by_order.get(sequence_order)
            if row is None:
                row = Dialogue(
                    project_id=script.project_id,
                    script_id=script.id,
                    sequence_order=sequence_order,
                    text=text,
                )
                db.add(row)
                db.flush()
            speaker = str(raw.get("speaker") or "").strip()
            row.character_id = character_ids_by_name.get(speaker)
            row.speaker_name = speaker or "Character"
            row.text = text
            row.translation_zh = _optional_text(raw.get("translation_zh"))
            row.emotion = _optional_text(raw.get("emotion"))
            row.sequence_order = sequence_order
            row.beat_id = _optional_text(raw.get("beat_id"))
            row.sound_cues = _string_list(raw.get("sound_cues"))
            db.add(row)
            kept_ids.add(row.id)

            item = {
                **raw,
                "id": row.id,
                "speaker": speaker or "Character",
                "text": row.text,
                "translation_zh": row.translation_zh or "",
                "emotion": row.emotion or "",
                "sequence_order": sequence_order,
                "beat_id": row.beat_id or "",
                "sound_cues": list(row.sound_cues or []),
                "scene_no": scene.get("scene_no"),
            }
            normalized_scene_dialogues.append(item)
            dialogue_index.append(dict(item))
            sequence_order += 1
        scene["dialogues"] = normalized_scene_dialogues

    for row in existing:
        if row.id not in kept_ids:
            db.delete(row)
    script.scenes = scenes
    script.dialogues = dialogue_index
    db.add(script)
    db.flush()
    return list_dialogues(db, script.project_id)


def save_dialogues(
    db: Session,
    project_id: str,
    payload: DialogueSaveRequest,
) -> tuple[list[Dialogue], list[str]]:
    if db.get(Project, project_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    script = db.scalar(
        select(Script)
        .where(Script.project_id == project_id)
        .order_by(Script.version.desc(), Script.created_at.desc())
    )
    if script is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Script not found")

    current = {row.id: row for row in list_dialogues(db, project_id)}
    kept_ids: set[str] = set()
    stale_shot_ids: set[str] = set()
    for item in payload.dialogues:
        row = current.get(item.id or "")
        if item.id and row is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Dialogue does not belong to project: {item.id}",
            )
        if row is None:
            row = Dialogue(
                project_id=project_id,
                script_id=script.id,
                speaker_name=item.speaker_name,
                text=item.text,
                sequence_order=item.sequence_order,
            )
        if row.shot_id:
            stale_shot_ids.add(row.shot_id)
        if item.shot_id:
            shot = db.get(Shot, item.shot_id)
            if shot is None or shot.project_id != project_id:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Shot does not belong to project: {item.shot_id}",
                )
            stale_shot_ids.add(shot.id)
        row.shot_id = item.shot_id
        row.character_id = item.character_id
        row.speaker_name = item.speaker_name
        row.text = item.text
        row.translation_zh = item.translation_zh
        row.emotion = item.emotion
        row.sequence_order = item.sequence_order
        row.beat_id = item.beat_id
        row.sound_cues = list(item.sound_cues)
        row.start_time = item.start_time
        row.end_time = item.end_time
        db.add(row)
        db.flush()
        kept_ids.add(row.id)

    for row in current.values():
        if row.id not in kept_ids:
            if row.shot_id:
                stale_shot_ids.add(row.shot_id)
            db.delete(row)

    _write_dialogues_back_to_script(db, script, project_id)
    for shot_id in stale_shot_ids:
        shot = db.get(Shot, shot_id)
        if shot is None:
            continue
        card = dict(shot.shot_card or {})
        card["prompt_stale"] = True
        card["stale_reason"] = "对白或字幕稿已更新"
        shot.shot_card = card
        db.add(shot)
    mark_downstream_stages_pending(
        db,
        project_id,
        "shots",
        summary="对白或字幕稿已更新，需要检查视频 Prompt",
    )
    db.commit()
    return list_dialogues(db, project_id), sorted(stale_shot_ids)


def bind_dialogues_to_shots(db: Session, project_id: str, shots: list[Shot]) -> None:
    dialogue_by_id = {row.id: row for row in list_dialogues(db, project_id)}
    for shot in shots:
        for dialogue_id in shot.dialogue_ids or []:
            row = dialogue_by_id.get(str(dialogue_id))
            if row is None:
                continue
            row.shot_id = shot.id
            db.add(row)


def _write_dialogues_back_to_script(
    db: Session,
    script: Script,
    project_id: str,
) -> None:
    rows = list_dialogues(db, project_id)
    script.dialogues = [
        {
            "id": row.id,
            "character_id": row.character_id,
            "shot_id": row.shot_id,
            "speaker": row.speaker_name,
            "text": row.text,
            "translation_zh": row.translation_zh or "",
            "emotion": row.emotion or "",
            "sequence_order": row.sequence_order,
            "beat_id": row.beat_id or "",
            "sound_cues": list(row.sound_cues or []),
            "start_time": float(row.start_time) if row.start_time is not None else None,
            "end_time": float(row.end_time) if row.end_time is not None else None,
        }
        for row in rows
    ]
    rows_by_id = {row.id: row for row in rows}
    scenes = script.scenes if isinstance(script.scenes, list) else []
    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        scene_dialogues = scene.get("dialogues")
        if not isinstance(scene_dialogues, list):
            continue
        for item in scene_dialogues:
            if not isinstance(item, dict):
                continue
            row = rows_by_id.get(str(item.get("id") or ""))
            if row is None:
                continue
            item.update(
                {
                    "speaker": row.speaker_name,
                    "text": row.text,
                    "translation_zh": row.translation_zh or "",
                    "emotion": row.emotion or "",
                    "sequence_order": row.sequence_order,
                    "beat_id": row.beat_id or "",
                    "sound_cues": list(row.sound_cues or []),
                }
            )
    script.scenes = scenes
    db.add(script)


def _latest_script_id(db: Session, project_id: str) -> str | None:
    return db.scalar(
        select(Script.id)
        .where(Script.project_id == project_id)
        .order_by(Script.version.desc(), Script.created_at.desc())
        .limit(1)
    )


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        str(item).strip()
        for item in value
        if str(item).strip()
    ]
