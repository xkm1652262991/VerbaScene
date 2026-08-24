from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import re
from typing import Any

from app.agents.script_screenplay import (
    extract_dialogues_from_scenes,
    normalize_production_scenes,
    render_readable_script,
)
from app.agents.style import resolve_visual_style


SCRIPT_QUALITY_PIPELINE_VERSION = "multi-agent-script-v3.1"
REVIEW_CATEGORIES = {"causality", "character", "dialogue", "continuity", "production"}
REVIEW_SEVERITIES = {"must_fix", "editorial_note"}


@dataclass(frozen=True)
class ScriptGenerationInput:
    """Immutable input snapshot used by every resumable script phase."""

    project_id: str
    chapter_id: str
    title: str
    style: str
    target_duration_sec: int
    aspect_ratio: str
    resolution: str
    creative_settings: dict[str, Any]
    input_mode: str
    outline: str
    source_text: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "ScriptGenerationInput":
        source = payload.get("script_input") if isinstance(payload.get("script_input"), dict) else payload
        creative_settings = source.get("creative_settings")
        return cls(
            project_id=_text(source.get("project_id")),
            chapter_id=_text(source.get("chapter_id")),
            title=_text(source.get("title")),
            style=_text(source.get("style")),
            target_duration_sec=_positive_int(source.get("target_duration_sec"), 90),
            aspect_ratio=_text(source.get("aspect_ratio")) or "16:9",
            resolution=_text(source.get("resolution")) or "854x480",
            creative_settings=dict(creative_settings) if isinstance(creative_settings, dict) else {},
            input_mode=_text(source.get("input_mode")) or "imported_script",
            outline=_text(source.get("outline")),
            source_text=_text(source.get("source_text")),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "chapter_id": self.chapter_id,
            "title": self.title,
            "style": self.style,
            "target_duration_sec": self.target_duration_sec,
            "aspect_ratio": self.aspect_ratio,
            "resolution": self.resolution,
            "creative_settings": dict(self.creative_settings),
            "input_mode": self.input_mode,
            "outline": self.outline,
            "source_text": self.source_text,
        }

    @property
    def source(self) -> str:
        return self.source_text.strip() or self.outline.strip()

    @property
    def english_level(self) -> str:
        return _text(self.creative_settings.get("english_level")) or "A1"

    def project_settings(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "target_duration_sec": self.target_duration_sec,
            "english_level": self.english_level,
            "animation_style": resolve_visual_style(self.style, self.creative_settings),
            "aspect_ratio": self.aspect_ratio,
            "resolution": self.resolution,
            "dialogue_language": "en",
            "native_audio": True,
        }


def required_phrases_from_source(source: ScriptGenerationInput) -> list[str]:
    if source.input_mode != "ai_brief" or not re.search(r"[\u4e00-\u9fff]", source.outline):
        return []
    matches = re.findall(
        r"[A-Za-z][A-Za-z'’]*(?:\s+[A-Za-z][A-Za-z'’]*|,\s*[A-Za-z][A-Za-z'’]*)*[?!.]",
        source.outline,
    )
    return list(dict.fromkeys(item.strip() for item in matches if item.strip()))


def parse_story_blueprint_response(text: str) -> dict[str, Any]:
    payload = _loads_json_object(text)
    value = payload.get("story_blueprint") if isinstance(payload.get("story_blueprint"), dict) else payload
    blueprint = {
        "premise": _text(value.get("premise")),
        "theme": _text(value.get("theme")),
        "opening_hook": _text(value.get("opening_hook")),
        "dramatic_question": _text(value.get("dramatic_question")),
        "emotional_payoff": _text(value.get("emotional_payoff")),
        "protected_requirements": _string_list(
            value.get("protected_requirements") or value.get("explicit_requirements")
        ),
        "required_phrases": _string_list(value.get("required_phrases")),
        "language_opportunity": _normalize_language_opportunity(value.get("language_opportunity")),
        "characters": _normalize_characters(value.get("characters")),
        "beats": _normalize_beats(value.get("beats")),
        "continuity_facts": _string_list(value.get("continuity_facts")),
        "creative_risks": _string_list(value.get("creative_risks")),
    }
    blueprint["explicit_requirements"] = list(blueprint["protected_requirements"])
    if not blueprint["premise"] and not blueprint["beats"]:
        raise ValueError("Story blueprint contains no premise or beats")
    return blueprint


def fallback_story_blueprint(source: ScriptGenerationInput, *, reason: str) -> dict[str, Any]:
    story_source = source.source
    return {
        "premise": story_source or source.title,
        "theme": "",
        "opening_hook": story_source or source.title,
        "dramatic_question": "",
        "emotional_payoff": "",
        "protected_requirements": [story_source] if story_source else [],
        "explicit_requirements": [story_source] if story_source else [],
        "required_phrases": required_phrases_from_source(source),
        "language_opportunity": {
            "communicative_function": "根据人物在故事中的真实需要选择英语表达",
            "candidate_phrases": [],
            "integration_note": "对白必须推动行动或回应，不做机械操练",
        },
        "characters": [],
        "beats": [
            {
                "beat_no": 1,
                "purpose": "保留并展开当前输入的核心事件",
                "visible_event": story_source,
                "choice_or_reaction": "",
                "state_change": "",
                "caused_by": "用户输入",
                "dialogue_intent": "",
            }
        ],
        "continuity_facts": [],
        "creative_risks": [f"故事开发阶段已降级：{reason}"],
        "fallback": True,
    }


def parse_script_review_response(text: str) -> dict[str, Any]:
    payload = _loads_json_object(text)
    value = payload.get("review") if isinstance(payload.get("review"), dict) else payload
    return _normalize_review(value)


def review_requires_revision(review: dict[str, Any]) -> bool:
    return any(
        isinstance(issue, dict) and issue.get("severity") == "must_fix"
        for issue in review.get("issues") or []
    )


def augment_script_review(
    review: dict[str, Any],
    *,
    draft_scenes: list[dict[str, Any]],
    blueprint: dict[str, Any],
) -> dict[str, Any]:
    """Add objective content-contract misses that a model reviewer overlooked."""

    result = _normalize_review(review)
    issues = [dict(item) for item in result["issues"]]
    existing_codes = {str(item.get("code") or "") for item in issues}
    all_scene_nos = [
        _positive_int(scene.get("scene_no"), index)
        for index, scene in enumerate(draft_scenes, start=1)
        if isinstance(scene, dict)
    ]
    dialogue_text = "\n".join(
        str(dialogue.get("text") or "")
        for scene in draft_scenes
        if isinstance(scene, dict)
        for dialogue in (scene.get("dialogues") if isinstance(scene.get("dialogues"), list) else [])
        if isinstance(dialogue, dict)
    )
    missing_phrases = [
        phrase
        for phrase in _string_list(blueprint.get("required_phrases"))
        if phrase.casefold() not in dialogue_text.casefold()
    ]
    if missing_phrases and "REQUIRED_PHRASE_MISSING" not in existing_codes:
        issues.append(
            {
                "code": "REQUIRED_PHRASE_MISSING",
                "category": "dialogue",
                "severity": "must_fix",
                "scene_nos": all_scene_nos,
                "problem": f"用户明确要求的英文表达未进入角色对白：{'、'.join(missing_phrases)}",
                "evidence": "全部 dialogues[].text 中均未找到这些表达。",
                "repair_instruction": "为每个缺失表达寻找能真实推动动作或回应的场景，逐字写入角色对白；不得只放进标题、动作说明或删除用户要求。",
                "protected_elements": missing_phrases,
            }
        )

    non_chinese_scene_nos = [
        int(scene.get("scene_no") or index)
        for index, scene in enumerate(draft_scenes, start=1)
        if isinstance(scene, dict) and _scene_has_non_chinese_production_copy(scene)
    ]
    if non_chinese_scene_nos and "PRODUCTION_COPY_LANGUAGE" not in existing_codes:
        issues.append(
            {
                "code": "PRODUCTION_COPY_LANGUAGE",
                "category": "production",
                "severity": "must_fix",
                "scene_nos": non_chinese_scene_nos,
                "problem": "非对白制作字段含英文描述、Markdown 或字典转储，无法直接进入中文制作工作台。",
                "evidence": "检查标题、地点、可见动作、剧情作用、起止状态、情绪和音效后发现语言合同未满足。",
                "repair_instruction": "保留人物专名和英文对白原文，把其余制作描述改为自然简体中文普通字符串；不得改变故事事实和动作顺序。",
                "protected_elements": [],
            }
        )

    return _review_payload(issues=issues)


def parse_script_patch_response(
    text: str,
    *,
    draft_scenes: list[dict[str, Any]],
    review: dict[str, Any],
) -> dict[str, Any]:
    """Validate a reviser's bounded scene replacements before they can touch a draft."""

    payload = _loads_json_object(text)
    value = payload.get("script_patch") if isinstance(payload.get("script_patch"), dict) else payload
    raw_replacements = value.get("scene_replacements")
    if not isinstance(raw_replacements, list):
        raise ValueError("Script patch must contain scene_replacements array")

    known_scene_nos = {
        _positive_int(scene.get("scene_no"), index)
        for index, scene in enumerate(draft_scenes, start=1)
        if isinstance(scene, dict)
    }
    must_fix_issues = [
        dict(issue)
        for issue in review.get("issues") or []
        if isinstance(issue, dict) and issue.get("severity") == "must_fix"
    ]
    allowed_scene_nos: set[int] = set()
    for issue in must_fix_issues:
        issue_scene_nos = set(_positive_int_list(issue.get("scene_nos")))
        allowed_scene_nos.update(issue_scene_nos or known_scene_nos)

    replacements: list[dict[str, Any]] = []
    replacement_scene_nos: set[int] = set()
    for item in raw_replacements:
        if not isinstance(item, dict):
            raise ValueError("Every scene replacement must be an object")
        scene_no = _positive_int(item.get("scene_no"), 0)
        if not scene_no:
            raise ValueError("Every scene replacement needs a positive scene_no")
        if scene_no in replacement_scene_nos:
            raise ValueError(f"Duplicate scene replacement: {scene_no}")
        if scene_no not in known_scene_nos:
            raise ValueError(f"Unknown scene replacement: {scene_no}")
        if scene_no not in allowed_scene_nos:
            raise ValueError(f"Scene replacement is outside review scope: {scene_no}")
        normalized = normalize_production_scenes([item])[0]
        if normalized["scene_no"] != scene_no:
            raise ValueError(f"Scene replacement changed scene_no: {scene_no}")
        replacements.append(normalized)
        replacement_scene_nos.add(scene_no)

    known_issue_codes = {
        _text(issue.get("code"))
        for issue in must_fix_issues
        if _text(issue.get("code"))
    }
    resolved_issue_codes = _string_list(value.get("resolved_issue_codes"))
    unresolved_issue_codes = _string_list(value.get("unresolved_issue_codes"))
    classified_codes = set(resolved_issue_codes) | set(unresolved_issue_codes)
    unknown_codes = classified_codes - known_issue_codes
    if unknown_codes:
        raise ValueError(f"Script patch references unknown issue codes: {', '.join(sorted(unknown_codes))}")
    overlap = set(resolved_issue_codes) & set(unresolved_issue_codes)
    if overlap:
        raise ValueError(f"Script patch resolves and leaves unresolved the same issues: {', '.join(sorted(overlap))}")
    missing_codes = known_issue_codes - classified_codes
    if missing_codes:
        raise ValueError(f"Script patch did not classify issue codes: {', '.join(sorted(missing_codes))}")
    if resolved_issue_codes and not replacements:
        raise ValueError("Script patch cannot resolve issues without replacing a scene")

    for issue in must_fix_issues:
        code = _text(issue.get("code"))
        if code not in resolved_issue_codes:
            continue
        issue_scene_nos = set(_positive_int_list(issue.get("scene_nos"))) or known_scene_nos
        if not replacement_scene_nos.intersection(issue_scene_nos):
            raise ValueError(f"Resolved issue has no replacement in its scene scope: {code}")

    return {
        "scene_replacements": replacements,
        "resolved_issue_codes": resolved_issue_codes,
        "unresolved_issue_codes": unresolved_issue_codes,
        "preserved_elements": _string_list(value.get("preserved_elements")),
    }


def apply_script_patch(
    draft: dict[str, Any],
    patch: dict[str, Any],
) -> dict[str, Any]:
    replacements = {
        int(scene["scene_no"]): deepcopy(scene)
        for scene in patch.get("scene_replacements") or []
        if isinstance(scene, dict)
    }
    scenes = [
        replacements.get(int(scene["scene_no"]), deepcopy(scene))
        for scene in draft.get("scenes") or []
        if isinstance(scene, dict)
    ]
    return {
        "content": render_readable_script(scenes),
        "scenes": scenes,
        "dialogues": extract_dialogues_from_scenes(scenes),
    }


def build_contract_report(
    *,
    final: dict[str, Any],
    blueprint: dict[str, Any],
    review: dict[str, Any],
    patch: dict[str, Any] | None,
    review_available: bool,
) -> dict[str, Any]:
    """Check objective production contracts without pretending to score story quality."""

    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    scenes = [dict(scene) for scene in final.get("scenes") or [] if isinstance(scene, dict)]
    scene_nos = [_positive_int(scene.get("scene_no"), index) for index, scene in enumerate(scenes, start=1)]
    if not scenes:
        errors.append(_contract_issue("SCENES_REQUIRED", [], "最终剧本没有可生产场景。"))
    if len(scene_nos) != len(set(scene_nos)):
        errors.append(_contract_issue("SCENE_NO_DUPLICATE", scene_nos, "最终剧本存在重复场号。"))
    if scene_nos != sorted(scene_nos):
        errors.append(_contract_issue("SCENE_ORDER_INVALID", scene_nos, "最终剧本场号顺序无效。"))

    dialogues = [dict(item) for item in final.get("dialogues") or [] if isinstance(item, dict)]
    if not dialogues:
        errors.append(_contract_issue("ENGLISH_DIALOGUE_REQUIRED", scene_nos, "最终剧本必须包含角色英文对白。"))
    invalid_speakers = sorted(
        {
            _positive_int(dialogue.get("scene_no"), 0)
            for dialogue in dialogues
            if not _text(dialogue.get("speaker")) or _text(dialogue.get("speaker")) == "角色"
        }
        - {0}
    )
    if invalid_speakers:
        errors.append(_contract_issue("DIALOGUE_SPEAKER_REQUIRED", invalid_speakers, "英文对白缺少明确说话人。"))

    dialogue_text = "\n".join(_text(dialogue.get("text")) for dialogue in dialogues)
    missing_phrases = [
        phrase
        for phrase in _string_list(blueprint.get("required_phrases"))
        if phrase.casefold() not in dialogue_text.casefold()
    ]
    if missing_phrases:
        errors.append(
            _contract_issue(
                "REQUIRED_PHRASE_MISSING",
                scene_nos,
                f"用户明确要求的英文表达仍未进入对白：{'、'.join(missing_phrases)}",
            )
        )

    production_language_scene_nos = [
        _positive_int(scene.get("scene_no"), index)
        for index, scene in enumerate(scenes, start=1)
        if _scene_has_non_chinese_production_copy(scene)
    ]
    if production_language_scene_nos:
        errors.append(
            _contract_issue(
                "PRODUCTION_COPY_LANGUAGE",
                production_language_scene_nos,
                "非对白制作字段仍含英文制作句、Markdown 或结构转储。",
            )
        )

    must_fix_codes = [
        _text(issue.get("code"))
        for issue in review.get("issues") or []
        if isinstance(issue, dict) and issue.get("severity") == "must_fix" and _text(issue.get("code"))
    ]
    unresolved_issue_codes = (
        _string_list(patch.get("unresolved_issue_codes"))
        if isinstance(patch, dict)
        else must_fix_codes
    )
    if unresolved_issue_codes:
        warnings.append(
            _contract_issue(
                "REVIEW_ISSUES_UNRESOLVED",
                [],
                f"仍有审稿问题需要人工处理：{', '.join(unresolved_issue_codes)}",
            )
        )

    quality_gate = "pass"
    if not review_available:
        quality_gate = "review_unavailable"
    elif errors or unresolved_issue_codes:
        quality_gate = "needs_attention"
    return {
        "valid": not errors,
        "quality_gate": quality_gate,
        "errors": errors,
        "warnings": warnings,
        "checked_contracts": [
            "scene_identity_and_order",
            "english_dialogue_and_speaker",
            "required_phrases",
            "production_copy_language",
        ],
        "unresolved_issue_codes": list(dict.fromkeys(unresolved_issue_codes)),
    }


def review_issue_counts(review: dict[str, Any]) -> dict[str, int]:
    counts = {"must_fix": 0, "editorial_note": 0}
    for issue in review.get("issues") or []:
        if isinstance(issue, dict) and issue.get("severity") in counts:
            counts[str(issue["severity"])] += 1
    return counts


def _normalize_review(value: dict[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    seen_codes: set[str] = set()

    removed_keys = {
        "verdict",
        "summary",
        "strengths",
        "protected_elements",
        "must_fix",
        "editorial_notes",
    }.intersection(value)
    if removed_keys:
        raise ValueError(
            f"Script review only accepts issues; removed fields: {', '.join(sorted(removed_keys))}"
        )

    raw_issues = value.get("issues")
    if not isinstance(raw_issues, list):
        raise ValueError("Script review must contain issues array")
    for index, item in enumerate(raw_issues, start=1):
        issue = _normalize_review_issue(item, index=index)
        if issue is None or issue["code"] in seen_codes:
            continue
        issues.append(issue)
        seen_codes.add(issue["code"])

    return _review_payload(issues=issues)


def _normalize_review_issue(
    value: Any,
    *,
    index: int,
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    problem = _text(value.get("problem"))
    repair_instruction = _text(value.get("repair_instruction"))
    evidence = _text(value.get("evidence"))
    if not problem and not repair_instruction and not evidence:
        return None
    category = _text(value.get("category")).lower()
    if category not in REVIEW_CATEGORIES:
        category = "production"
    severity = _text(value.get("severity")).lower()
    if severity not in REVIEW_SEVERITIES:
        severity = "must_fix"
    prefix = "EDITORIAL_NOTE" if severity == "editorial_note" else "SCRIPT_ISSUE"
    return {
        "code": _text(value.get("code")) or f"{prefix}_{index:03d}",
        "category": category,
        "severity": severity,
        "scene_nos": _positive_int_list(value.get("scene_nos")),
        "problem": problem,
        "evidence": evidence,
        "repair_instruction": repair_instruction,
        "protected_elements": _string_list(value.get("protected_elements")),
    }


def _review_payload(
    *,
    issues: list[dict[str, Any]],
) -> dict[str, Any]:
    return {"issues": [dict(issue) for issue in issues]}


def _contract_issue(code: str, scene_nos: list[int], message: str) -> dict[str, Any]:
    return {"code": code, "scene_nos": list(dict.fromkeys(scene_nos)), "message": message}


def _loads_json_object(text: str) -> dict[str, Any]:
    cleaned = str(text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL | re.IGNORECASE)
    if fenced:
        cleaned = fenced.group(1).strip()
    if not cleaned.startswith("{"):
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start : end + 1]
    payload = json.loads(cleaned)
    if not isinstance(payload, dict):
        raise ValueError("Response JSON must be an object")
    return payload


def _normalize_language_opportunity(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    return {
        "communicative_function": _text(source.get("communicative_function")),
        "candidate_phrases": _string_list(source.get("candidate_phrases")),
        "integration_note": _text(source.get("integration_note")),
    }


def _normalize_characters(value: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "name": _text(item.get("name")),
                "want": _text(item.get("want")),
                "obstacle": _text(item.get("obstacle")),
                "action": _text(item.get("action")),
                "change": _text(item.get("change")),
                "motivation": _text(item.get("motivation")),
                "relationship": _text(item.get("relationship")),
            }
        )
    return result


def _normalize_beats(value: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, item in enumerate(value if isinstance(value, list) else [], start=1):
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "beat_no": _positive_int(item.get("beat_no"), index),
                "purpose": _text(item.get("purpose")),
                "visible_event": _text(item.get("visible_event")),
                "choice_or_reaction": _text(item.get("choice_or_reaction")),
                "state_change": _text(item.get("state_change")),
                "caused_by": _text(item.get("caused_by")),
                "dialogue_intent": _text(item.get("dialogue_intent")),
            }
        )
    return result


def _dict_list(value: Any) -> list[dict[str, Any]]:
    return [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(_text(item) for item in value if _text(item)))


def _positive_int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    result: list[int] = []
    for item in value:
        parsed = _positive_int(item, 0)
        if parsed and parsed not in result:
            result.append(parsed)
    return result


def _positive_int(value: Any, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _scene_has_non_chinese_production_copy(scene: dict[str, Any]) -> bool:
    fields = (
        "title",
        "location",
        "time_of_day",
        "visible_action",
        "story_purpose",
        "start_state",
        "end_state",
        "mood",
        "source_evidence",
    )
    values = [_text(scene.get(field)) for field in fields]
    values.extend(_string_list(scene.get("inferred_elements")))
    values.extend(_string_list(scene.get("sound_cues")))
    values.extend(
        _text(dialogue.get("emotion"))
        for dialogue in (scene.get("dialogues") if isinstance(scene.get("dialogues"), list) else [])
        if isinstance(dialogue, dict)
    )
    for value in values:
        if not value:
            continue
        if "*" in value or value.startswith(("{", "[")):
            return True
        latin_words = re.findall(r"[A-Za-z]{3,}", value)
        if latin_words and not re.search(r"[\u4e00-\u9fff]", value):
            return True
    return False
