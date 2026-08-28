from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import re
from types import SimpleNamespace
from typing import Any

from app.agents.shot_breakdown import parse_shot_breakdown_response


SHOT_DIRECTION_TASK_TYPE = "shot_breakdown"
SHOT_DIRECTION_PIPELINE_VERSION = "reflexion-shot-director-v1"
MANUALLY_RETRYABLE_SHOT_DIRECTION_TASK_TYPES = frozenset({SHOT_DIRECTION_TASK_TYPE})

REVIEW_CATEGORIES = frozenset(
    {
        "coverage",
        "continuity",
        "dialogue",
        "cinematography",
        "asset_feasibility",
        "production",
    }
)
REVIEW_SEVERITIES = frozenset({"must_fix", "editorial_note"})
PATCH_OPERATIONS = frozenset({"replace", "insert_after", "remove"})


@dataclass(frozen=True)
class ShotDirectionInput:
    """Immutable snapshot shared by every resumable shot-direction phase."""

    project_id: str
    script_id: str
    title: str
    style: str
    target_duration_sec: int
    aspect_ratio: str
    resolution: str
    creative_settings: dict[str, Any]
    script_content: str
    script_scenes: list[dict[str, Any]]
    dialogues: list[dict[str, Any]]
    characters: list[dict[str, Any]]
    scenes: list[dict[str, Any]]
    props: list[dict[str, Any]]
    adopted_assets: list[dict[str, Any]]
    segment_contract: dict[str, Any]

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "ShotDirectionInput":
        source = (
            payload.get("shot_direction_input")
            if isinstance(payload.get("shot_direction_input"), dict)
            else payload
        )
        return cls(
            project_id=_text(source.get("project_id")),
            script_id=_text(source.get("script_id")),
            title=_text(source.get("title")),
            style=_text(source.get("style")),
            target_duration_sec=_positive_int(source.get("target_duration_sec"), 90),
            aspect_ratio=_text(source.get("aspect_ratio")) or "16:9",
            resolution=_text(source.get("resolution")) or "854x480",
            creative_settings=_dict(source.get("creative_settings")),
            script_content=_text(source.get("script_content")),
            script_scenes=_dict_list(source.get("script_scenes")),
            dialogues=_dict_list(source.get("dialogues")),
            characters=_dict_list(source.get("characters")),
            scenes=_dict_list(source.get("scenes")),
            props=_dict_list(source.get("props")),
            adopted_assets=_dict_list(source.get("adopted_assets")),
            segment_contract=_dict(source.get("segment_contract")),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "script_id": self.script_id,
            "title": self.title,
            "style": self.style,
            "target_duration_sec": self.target_duration_sec,
            "aspect_ratio": self.aspect_ratio,
            "resolution": self.resolution,
            "creative_settings": deepcopy(self.creative_settings),
            "script_content": self.script_content,
            "script_scenes": deepcopy(self.script_scenes),
            "dialogues": deepcopy(self.dialogues),
            "characters": deepcopy(self.characters),
            "scenes": deepcopy(self.scenes),
            "props": deepcopy(self.props),
            "adopted_assets": deepcopy(self.adopted_assets),
            "segment_contract": deepcopy(self.segment_contract),
        }

    @property
    def allowed_character_ids(self) -> set[str]:
        return _record_ids(self.characters)

    @property
    def allowed_scene_ids(self) -> set[str]:
        return _record_ids(self.scenes)

    @property
    def allowed_prop_ids(self) -> set[str]:
        return _record_ids(self.props)

    @property
    def allowed_dialogue_ids(self) -> set[str]:
        return _record_ids(self.dialogues)

    @property
    def source_scene_nos(self) -> set[int]:
        return {
            _positive_int(item.get("scene_no"), 0)
            for item in self.script_scenes
            if _positive_int(item.get("scene_no"), 0) > 0
        }

    def script_value(self) -> SimpleNamespace:
        return SimpleNamespace(
            id=self.script_id,
            content=self.script_content,
            scenes=deepcopy(self.script_scenes),
            dialogues=deepcopy(self.dialogues),
        )

    def character_values(self) -> list[SimpleNamespace]:
        return _namespace_records(self.characters)

    def scene_values(self) -> list[SimpleNamespace]:
        return _namespace_records(self.scenes)

    def prop_values(self) -> list[SimpleNamespace]:
        return _namespace_records(self.props)

    def dialogue_values(self) -> list[SimpleNamespace]:
        return _namespace_records(self.dialogues)

    def project_value(self) -> SimpleNamespace:
        return SimpleNamespace(
            id=self.project_id,
            title=self.title,
            style=self.style,
            target_duration_sec=self.target_duration_sec,
            aspect_ratio=self.aspect_ratio,
            resolution=self.resolution,
            creative_settings=deepcopy(self.creative_settings),
        )


@dataclass(frozen=True)
class ShotPatchResult:
    patch: dict[str, Any]
    shots: list[dict[str, Any]]
    patched_shot_nos: list[int]


def parse_shot_draft_response(text: str, source: ShotDirectionInput) -> list[dict[str, Any]]:
    payload = _loads_json(text)
    raw_shots = payload.get("shots") if isinstance(payload, dict) else payload
    if not isinstance(raw_shots, list) or not raw_shots:
        raise ValueError("ShotDraft must contain a non-empty shots array")
    for index, raw in enumerate(raw_shots, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"ShotDraft item {index} must be an object")
        _validate_raw_references(raw, source, context=f"片段 {index}")
    shots = parse_shot_breakdown_response(
        text=json.dumps({"shots": raw_shots}, ensure_ascii=False),
        characters=source.character_values(),
        scenes=source.scene_values(),
        props=source.prop_values(),
        allowed_dialogue_ids=source.allowed_dialogue_ids,
        segment_contract=source.segment_contract,
    )
    return renumber_shots(shots)


def parse_reflection_response(
    text: str,
    *,
    draft: list[dict[str, Any]],
    source: ShotDirectionInput,
    asset_report: dict[str, Any],
    draft_contract: dict[str, Any],
) -> dict[str, Any]:
    payload = _loads_json(text)
    value = (
        payload.get("reflection_report")
        if isinstance(payload, dict) and isinstance(payload.get("reflection_report"), dict)
        else payload
    )
    if not isinstance(value, dict):
        raise ValueError("ReflectionReport must be an object")
    valid_shot_nos = {int(item["shot_no"]) for item in draft}
    issues: list[dict[str, Any]] = []
    for index, raw in enumerate(value.get("issues") or [], start=1):
        if not isinstance(raw, dict):
            continue
        category = _text(raw.get("category"))
        severity = _text(raw.get("severity"))
        if category not in REVIEW_CATEGORIES or severity not in REVIEW_SEVERITIES:
            continue
        code = _slug(raw.get("code")) or f"review_issue_{index}"
        shot_nos = [
            number
            for number in _int_list(raw.get("shot_nos"))
            if number in valid_shot_nos
        ]
        issues.append(
            {
                "code": code,
                "category": category,
                "severity": severity,
                "message": _text(raw.get("message") or raw.get("description")) or code,
                "shot_nos": shot_nos,
                "suggestion": _text(raw.get("suggestion") or raw.get("fix")),
                "evidence": _text(raw.get("evidence")),
            }
        )
    review = {
        "summary": _text(value.get("summary")),
        "issues": issues,
    }
    return augment_reflection_report(
        review,
        draft=draft,
        source=source,
        asset_report=asset_report,
        draft_contract=draft_contract,
    )


def augment_reflection_report(
    review: dict[str, Any],
    *,
    draft: list[dict[str, Any]],
    source: ShotDirectionInput,
    asset_report: dict[str, Any],
    draft_contract: dict[str, Any],
) -> dict[str, Any]:
    result = deepcopy(review)
    issues = [dict(item) for item in result.get("issues") or [] if isinstance(item, dict)]
    existing_codes = {_text(item.get("code")) for item in issues}

    used_dialogues: list[str] = [
        dialogue_id
        for shot in draft
        for dialogue_id in shot.get("dialogue_ids") or []
        if isinstance(dialogue_id, str)
    ]
    missing_dialogues = sorted(source.allowed_dialogue_ids - set(used_dialogues))
    if missing_dialogues and "dialogue_coverage_missing" not in existing_codes:
        issues.append(
            {
                "code": "dialogue_coverage_missing",
                "category": "coverage",
                "severity": "must_fix",
                "message": f"有 {len(missing_dialogues)} 条英文对白尚未绑定到任何片段。",
                "shot_nos": [int(draft[-1]["shot_no"])] if draft else [],
                "suggestion": "在最接近原场次的片段节拍中绑定缺失 Dialogue ID，不改写对白文本。",
                "evidence": "、".join(missing_dialogues),
            }
        )

    used_scene_nos = {
        _positive_int((shot.get("shot_card") or {}).get("source_scene_no"), 0)
        for shot in draft
    }
    missing_scene_nos = sorted(source.source_scene_nos - used_scene_nos)
    if missing_scene_nos and "source_scene_coverage_missing" not in existing_codes:
        issues.append(
            {
                "code": "source_scene_coverage_missing",
                "category": "coverage",
                "severity": "must_fix",
                "message": f"结构化剧本场次 {missing_scene_nos} 尚未被片段覆盖。",
                "shot_nos": [int(draft[-1]["shot_no"])] if draft else [],
                "suggestion": "在相邻片段中补足剧情，或在合同允许时插入一个连续叙事片段。",
                "evidence": f"missing source_scene_no={missing_scene_nos}",
            }
        )

    for error in draft_contract.get("errors") or []:
        if not isinstance(error, dict):
            continue
        code = _text(error.get("code"))
        if not code or code in existing_codes:
            continue
        issues.append(
            {
                "code": code,
                "category": "production" if code != "dialogue_duplicate" else "dialogue",
                "severity": "must_fix",
                "message": _text(error.get("message")) or code,
                "shot_nos": [
                    number
                    for number in _int_list(error.get("shot_nos"))
                    if any(int(shot["shot_no"]) == number for shot in draft)
                ],
                "suggestion": "只修订报告点名的片段，使最终结构合同通过。",
                "evidence": "确定性合同检查",
            }
        )

    missing_references = asset_report.get("missing_references") or []
    if missing_references and "reference_assets_missing" not in existing_codes:
        issues.append(
            {
                "code": "reference_assets_missing",
                "category": "asset_feasibility",
                "severity": "editorial_note",
                "message": f"默认参考资产路径仍缺少 {len(missing_references)} 项角色或场景图片。",
                "shot_nos": [],
                "suggestion": "分镜可继续编辑；提交默认参考图视频路径前补充并采用对应图片。",
                "evidence": "metadata_only asset inspection",
            }
        )

    result["issues"] = _dedupe_issues(issues)
    return result


def reflection_requires_patch(review: dict[str, Any]) -> bool:
    return any(
        isinstance(item, dict)
        and item.get("severity") == "must_fix"
        and bool(item.get("shot_nos"))
        for item in review.get("issues") or []
    )


def parse_and_apply_shot_patch(
    text: str,
    *,
    draft: list[dict[str, Any]],
    review: dict[str, Any],
    source: ShotDirectionInput,
) -> ShotPatchResult:
    payload = _loads_json(text)
    value = (
        payload.get("shot_patch")
        if isinstance(payload, dict) and isinstance(payload.get("shot_patch"), dict)
        else payload
    )
    if not isinstance(value, dict):
        raise ValueError("ShotPatch must be an object")

    must_fix_issues = [
        item
        for item in review.get("issues") or []
        if isinstance(item, dict) and item.get("severity") == "must_fix"
    ]
    must_fix_codes = {_text(item.get("code")) for item in must_fix_issues}
    allowed_targets = {
        number
        for item in must_fix_issues
        for number in _int_list(item.get("shot_nos"))
    }
    existing_nos = {int(item["shot_no"]) for item in draft}
    raw_operations = value.get("operations")
    if not isinstance(raw_operations, list):
        raise ValueError("ShotPatch.operations must be an array")

    operations: list[dict[str, Any]] = []
    touched_targets: set[int] = set()
    for index, raw in enumerate(raw_operations, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"ShotPatch operation {index} must be an object")
        operation = _text(raw.get("op"))
        target = _positive_int(raw.get("shot_no") or raw.get("target_shot_no"), 0)
        if operation not in PATCH_OPERATIONS:
            raise ValueError(f"Unsupported ShotPatch operation: {operation}")
        if target not in existing_nos or target not in allowed_targets:
            raise ValueError(f"ShotPatch target {target} was not authorized by ReflectionReport")
        if target in touched_targets:
            raise ValueError(f"ShotPatch target {target} has more than one operation")
        touched_targets.add(target)
        item: dict[str, Any] = {"op": operation, "shot_no": target}
        if operation != "remove":
            raw_shot = raw.get("shot")
            if not isinstance(raw_shot, dict):
                raise ValueError(f"ShotPatch {operation} operation requires a shot object")
            _validate_raw_references(raw_shot, source, context=f"Patch target {target}")
            normalized = parse_shot_breakdown_response(
                text=json.dumps({"shots": [raw_shot]}, ensure_ascii=False),
                characters=source.character_values(),
                scenes=source.scene_values(),
                props=source.prop_values(),
                allowed_dialogue_ids=source.allowed_dialogue_ids,
                segment_contract=None,
            )[0]
            item["shot"] = normalized
        operations.append(item)

    resolved_codes = [
        code for code in _string_list(value.get("resolved_issue_codes")) if code in must_fix_codes
    ]
    unresolved_codes = [
        code for code in _string_list(value.get("unresolved_issue_codes")) if code in must_fix_codes
    ]
    patch = {
        "operations": operations,
        "resolved_issue_codes": list(dict.fromkeys(resolved_codes)),
        "unresolved_issue_codes": list(dict.fromkeys(unresolved_codes)),
        "notes": _text(value.get("notes")),
    }
    shots = _apply_patch_operations(draft, operations)
    shots = parse_shot_breakdown_response(
        text=json.dumps({"shots": shots}, ensure_ascii=False),
        characters=source.character_values(),
        scenes=source.scene_values(),
        props=source.prop_values(),
        allowed_dialogue_ids=source.allowed_dialogue_ids,
        segment_contract=source.segment_contract,
    )
    return ShotPatchResult(
        patch=patch,
        shots=renumber_shots(shots),
        patched_shot_nos=sorted(touched_targets),
    )


def build_shot_contract_report(
    *,
    shots: list[dict[str, Any]],
    source: ShotDirectionInput,
    review: dict[str, Any] | None = None,
    patch: dict[str, Any] | None = None,
    review_available: bool = True,
    patch_error: str | None = None,
) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    used_dialogues: dict[str, list[int]] = {}
    used_scene_nos: set[int] = set()
    allowed_character_ids = source.allowed_character_ids
    allowed_scene_ids = source.allowed_scene_ids
    allowed_prop_ids = source.allowed_prop_ids

    if not shots:
        errors.append({"code": "shots_empty", "message": "最终分镜没有任何片段。", "shot_nos": []})
    for index, shot in enumerate(shots, start=1):
        shot_no = _positive_int(shot.get("shot_no"), index)
        card = shot.get("shot_card") if isinstance(shot.get("shot_card"), dict) else {}
        action = card.get("action") if isinstance(card.get("action"), dict) else {}
        beats = card.get("beats") if isinstance(card.get("beats"), list) else []
        image_prompts = card.get("image_prompts") if isinstance(card.get("image_prompts"), dict) else {}
        storyboard = (
            image_prompts.get("shot_storyboard")
            if isinstance(image_prompts.get("shot_storyboard"), dict)
            else {}
        )
        if _text(shot.get("scene_id")) not in allowed_scene_ids:
            errors.append({"code": "scene_reference_invalid", "message": f"片段 {shot_no} 没有有效场景引用。", "shot_nos": [shot_no]})
        unknown_characters = set(_string_list(shot.get("character_ids"))) - allowed_character_ids
        unknown_props = set(_string_list(shot.get("prop_ids"))) - allowed_prop_ids
        if unknown_characters or unknown_props:
            errors.append({"code": "entity_reference_invalid", "message": f"片段 {shot_no} 引用了未知实体。", "shot_nos": [shot_no]})
        source_scene_no = _positive_int(card.get("source_scene_no"), 0)
        if source.source_scene_nos and source_scene_no not in source.source_scene_nos:
            errors.append({"code": "source_scene_invalid", "message": f"片段 {shot_no} 的 source_scene_no 无效。", "shot_nos": [shot_no]})
        if source_scene_no:
            used_scene_nos.add(source_scene_no)
        if not _text(shot.get("description")) or not _text(action.get("main_action")):
            errors.append({"code": "visible_action_missing", "message": f"片段 {shot_no} 缺少可执行动作。", "shot_nos": [shot_no]})
        if not _text(storyboard.get("positive_prompt")):
            errors.append({"code": "storyboard_prompt_missing", "message": f"片段 {shot_no} 缺少分镜图主体 Prompt。", "shot_nos": [shot_no]})
        min_beats = _positive_int(source.segment_contract.get("min_internal_shots"), 2)
        max_beats = _positive_int(source.segment_contract.get("max_internal_shots"), 4)
        if len(beats) < min_beats or len(beats) > max_beats:
            errors.append({"code": "internal_beat_count_invalid", "message": f"片段 {shot_no} 的内部镜头数不在 {min_beats}-{max_beats} 范围。", "shot_nos": [shot_no]})
        for dialogue_id in _string_list(shot.get("dialogue_ids")):
            if dialogue_id not in source.allowed_dialogue_ids:
                errors.append({"code": "dialogue_reference_invalid", "message": f"片段 {shot_no} 引用了未知 Dialogue。", "shot_nos": [shot_no]})
            used_dialogues.setdefault(dialogue_id, []).append(shot_no)

    for dialogue_id, shot_nos in used_dialogues.items():
        if len(shot_nos) > 1:
            errors.append(
                {
                    "code": "dialogue_duplicate",
                    "message": f"Dialogue {dialogue_id} 被多个片段重复绑定。",
                    "shot_nos": sorted(set(shot_nos)),
                }
            )

    missing_dialogues = sorted(source.allowed_dialogue_ids - set(used_dialogues))
    if missing_dialogues:
        warnings.append(
            {
                "code": "dialogue_coverage_missing",
                "message": f"仍有 {len(missing_dialogues)} 条 Dialogue 未覆盖。",
                "dialogue_ids": missing_dialogues,
            }
        )
    missing_scene_nos = sorted(source.source_scene_nos - used_scene_nos)
    if missing_scene_nos:
        warnings.append(
            {
                "code": "source_scene_coverage_missing",
                "message": f"仍有剧本场次 {missing_scene_nos} 未覆盖。",
                "source_scene_nos": missing_scene_nos,
            }
        )

    review_value = review if isinstance(review, dict) else {"issues": []}
    must_fix_codes = {
        _text(item.get("code"))
        for item in review_value.get("issues") or []
        if isinstance(item, dict) and item.get("severity") == "must_fix"
    }
    resolved_codes = set(_string_list((patch or {}).get("resolved_issue_codes")))
    unresolved_codes = (must_fix_codes - resolved_codes) | set(
        _string_list((patch or {}).get("unresolved_issue_codes"))
    )
    warning_codes = {_text(item.get("code")) for item in warnings}
    unresolved_codes |= warning_codes

    if errors:
        quality_gate = "invalid"
    elif not review_available:
        quality_gate = "review_unavailable"
    elif unresolved_codes or patch_error:
        quality_gate = "needs_attention"
    else:
        quality_gate = "pass"
    return {
        "valid": not errors,
        "quality_gate": quality_gate,
        "errors": errors,
        "warnings": warnings,
        "unresolved_issue_codes": sorted(code for code in unresolved_codes if code),
    }


def review_issue_counts(review: dict[str, Any]) -> dict[str, int]:
    counts = {"must_fix": 0, "editorial_note": 0}
    counts.update({category: 0 for category in REVIEW_CATEGORIES})
    for issue in review.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        severity = _text(issue.get("severity"))
        category = _text(issue.get("category"))
        if severity in counts:
            counts[severity] += 1
        if category in REVIEW_CATEGORIES:
            counts[category] += 1
    return counts


def renumber_shots(shots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = deepcopy(shots)
    for index, shot in enumerate(result, start=1):
        shot["shot_no"] = index
        card = shot.get("shot_card") if isinstance(shot.get("shot_card"), dict) else {}
        beats = card.get("beats") if isinstance(card.get("beats"), list) else []
        for beat_index, beat in enumerate(beats, start=1):
            if isinstance(beat, dict):
                beat["beat_id"] = f"shot-{index}-beat-{beat_index}"
        timing = card.get("motion_timing") if isinstance(card.get("motion_timing"), dict) else {}
        timing["beats"] = deepcopy(beats)
        card["motion_timing"] = timing
        shot["shot_card"] = card
    return result


def _apply_patch_operations(
    draft: list[dict[str, Any]],
    operations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    operation_by_target = {int(item["shot_no"]): item for item in operations}
    result: list[dict[str, Any]] = []
    for shot in draft:
        original_no = int(shot["shot_no"])
        operation = operation_by_target.get(original_no)
        if operation is None:
            result.append(deepcopy(shot))
            continue
        if operation["op"] == "replace":
            result.append(deepcopy(operation["shot"]))
        elif operation["op"] == "insert_after":
            result.append(deepcopy(shot))
            result.append(deepcopy(operation["shot"]))
        elif operation["op"] == "remove":
            continue
    if not result:
        raise ValueError("ShotPatch cannot remove the entire draft")
    return renumber_shots(result)


def _validate_raw_references(raw: dict[str, Any], source: ShotDirectionInput, *, context: str) -> None:
    scene_id = raw.get("scene_id")
    if not isinstance(scene_id, str) or scene_id not in source.allowed_scene_ids:
        raise ValueError(f"{context} contains an unknown scene_id")
    checks = (
        ("character_ids", source.allowed_character_ids),
        ("prop_ids", source.allowed_prop_ids),
        ("dialogue_ids", source.allowed_dialogue_ids),
    )
    for field, allowed in checks:
        values = _string_list(raw.get(field))
        if set(values) - allowed:
            raise ValueError(f"{context} contains unknown {field}")
    card = raw.get("shot_card") if isinstance(raw.get("shot_card"), dict) else {}
    source_scene_no = _positive_int(card.get("source_scene_no"), 0)
    if source.source_scene_nos and source_scene_no not in source.source_scene_nos:
        raise ValueError(f"{context} contains an unknown source_scene_no")
    beats = card.get("beats")
    if not isinstance(beats, list):
        timing = card.get("motion_timing") if isinstance(card.get("motion_timing"), dict) else {}
        beats = timing.get("beats") if isinstance(timing.get("beats"), list) else []
    for beat in beats:
        if not isinstance(beat, dict):
            continue
        if set(_string_list(beat.get("dialogue_ids"))) - source.allowed_dialogue_ids:
            raise ValueError(f"{context} contains an unknown beat dialogue_id")


def _loads_json(text: str) -> Any:
    cleaned = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)
    if fenced:
        cleaned = fenced.group(1).strip()
    if not cleaned.startswith(("{", "[")):
        object_start, object_end = cleaned.find("{"), cleaned.rfind("}")
        array_start, array_end = cleaned.find("["), cleaned.rfind("]")
        if object_start >= 0 and object_end > object_start:
            cleaned = cleaned[object_start : object_end + 1]
        elif array_start >= 0 and array_end > array_start:
            cleaned = cleaned[array_start : array_end + 1]
    return json.loads(cleaned)


def _dedupe_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for issue in issues:
        code = _text(issue.get("code"))
        if not code or code in seen:
            continue
        seen.add(code)
        result.append(issue)
    return result


def _namespace_records(items: list[dict[str, Any]]) -> list[SimpleNamespace]:
    return [SimpleNamespace(**deepcopy(item)) for item in items]


def _record_ids(items: list[dict[str, Any]]) -> set[str]:
    return {_text(item.get("id")) for item in items if _text(item.get("id"))}


def _dict(value: object) -> dict[str, Any]:
    return deepcopy(value) if isinstance(value, dict) else {}


def _dict_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [deepcopy(item) for item in value if isinstance(item, dict)]


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _positive_int(value: object, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def _int_list(value: object) -> list[int]:
    if not isinstance(value, list):
        return []
    result: list[int] = []
    for item in value:
        parsed = _positive_int(item, 0)
        if parsed and parsed not in result:
            result.append(parsed)
    return result


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(_text(item) for item in value if _text(item)))


def _slug(value: object) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", _text(value).lower()).strip("_")
