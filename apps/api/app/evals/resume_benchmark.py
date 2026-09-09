from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
import csv
import json
from pathlib import Path
import re
from typing import Any, Iterable

from app.agents.script_contracts import ScriptGenerationInput
from app.agents.script_screenplay import render_readable_script
from app.agents.shot_breakdown import segment_planning_contract
from app.production.shot_direction.contracts import (
    ShotDirectionInput,
    build_shot_contract_report,
)


BENCHMARK_VERSION = "verba-scene-resume-benchmark-v1"
EVALUATOR_VERSION = "storyboard-gold-gate-v1"
PATCH_EVALUATOR_VERSION = "bounded-patch-field-diff-v1"


def json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, set):
        return sorted(value)
    if hasattr(value, "value"):
        return value.value
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=json_default,
    )


def content_hash(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=json_default) + "\n",
        encoding="utf-8",
    )


def write_csv(path: Path, rows: Iterable[dict[str, Any]], *, fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: _csv_value(row.get(key))
                    for key in fieldnames
                }
            )


def script_input_from_eval_case(case: dict[str, Any], *, index: int) -> ScriptGenerationInput:
    return ScriptGenerationInput(
        project_id=f"resume-eval-project-{index:02d}",
        chapter_id=f"resume-eval-chapter-{index:02d}",
        title=str(case.get("title") or case.get("id") or f"Case {index}"),
        style="高品质风格化三维儿童动画",
        target_duration_sec=60,
        aspect_ratio="16:9",
        resolution="854x480",
        creative_settings={"english_level": "A1"},
        input_mode=str(case.get("input_mode") or "ai_brief"),
        outline=str(case.get("outline") or ""),
        source_text=str(case.get("source_text") or ""),
    )


def storyboard_case_from_script_result(
    eval_case: dict[str, Any],
    *,
    index: int,
    script_result: dict[str, Any] | None,
) -> dict[str, Any]:
    final = (
        deepcopy(script_result.get("parsed_output", {}).get("final"))
        if isinstance(script_result, dict)
        and isinstance(script_result.get("parsed_output"), dict)
        and isinstance(script_result.get("parsed_output", {}).get("final"), dict)
        else None
    )
    if not final or not isinstance(final.get("scenes"), list) or not final["scenes"]:
        final = _fallback_script_for_eval_case(eval_case)
        source_origin = "deterministic_fallback_after_script_failure"
    else:
        source_origin = "live_script_pipeline_final"

    target_duration = _storyboard_duration_for_index(index)
    case_id = str(eval_case.get("id") or f"legacy-{index:02d}")
    source, gold = _shot_source_from_script(
        case_id=case_id,
        title=str(eval_case.get("title") or case_id),
        final=final,
        target_duration_sec=target_duration,
    )
    return {
        "case_id": f"existing31-{case_id}",
        "case_type": "existing_31_reused_as_storyboard_source",
        "coverage_tags": list(eval_case.get("focus") or []),
        "source_origin": source_origin,
        "source": source.to_payload(),
        "gold": gold,
        "legacy_eval_case": deepcopy(eval_case),
    }


def build_extra_storyboard_cases() -> list[dict[str, Any]]:
    specs = [
        {
            "case_id": "extended-multi-character-dialogue",
            "title": "三人轮流协作寻找路标",
            "target_duration_sec": 60,
            "coverage_tags": ["multi_character", "multi_dialogue", "speaker_ownership"],
            "characters": ["Mia", "Leo", "Nora"],
            "locations": ["林间岔路", "木桥旁"],
            "props": ["折叠地图", "红色路标"],
            "dialogues": [
                ("Mia", "Which way should we go?"),
                ("Leo", "Let me check the map."),
                ("Nora", "I can see a red sign."),
                ("Mia", "This way, please."),
                ("Leo", "We found the bridge!"),
                ("Nora", "Good job, everyone."),
            ],
        },
        {
            "case_id": "extended-long-dialogue",
            "title": "长对白仍需完整绑定",
            "target_duration_sec": 60,
            "coverage_tags": ["long_dialogue", "dialogue_coverage"],
            "characters": ["Ava", "Ben"],
            "locations": ["美术教室"],
            "props": ["蓝色纸张", "安全剪刀"],
            "dialogues": [
                ("Ava", "Please hold the blue paper while I cut along this line."),
                ("Ben", "I will hold it still and keep my fingers away."),
                ("Ava", "Thank you, now we can open the paper star."),
            ],
        },
        {
            "case_id": "extended-many-assets",
            "title": "多资产引用不能错绑",
            "target_duration_sec": 75,
            "coverage_tags": ["multi_asset", "entity_reference"],
            "characters": ["Nina", "Owen", "Pip"],
            "locations": ["厨房", "阳台"],
            "props": ["玻璃碗", "红苹果", "木勺", "黄色毛巾"],
            "dialogues": [
                ("Nina", "Please wash the apples."),
                ("Owen", "I can dry them."),
                ("Pip", "The bowl is ready."),
                ("Nina", "Here comes the fruit salad!"),
            ],
        },
        {
            "case_id": "extended-key-phrase",
            "title": "关键短语通过 Dialogue ID 冻结",
            "target_duration_sec": 60,
            "coverage_tags": ["protected_phrase", "dialogue_identity"],
            "characters": ["Mia", "Leo"],
            "locations": ["积木角"],
            "props": ["蓝色积木", "积木塔"],
            "dialogues": [
                ("Mia", "Can I use it?"),
                ("Leo", "Wait, please."),
                ("Leo", "Here you are."),
            ],
        },
        {
            "case_id": "extended-duration-lower-boundary",
            "title": "一分钟时长预算下界",
            "target_duration_sec": 60,
            "coverage_tags": ["duration_boundary", "shot_count_boundary"],
            "characters": ["Lily", "Max"],
            "locations": ["社区花园", "工具棚"],
            "props": ["小水壶", "种子袋"],
            "dialogues": [
                ("Lily", "The soil is dry."),
                ("Max", "I will get some water."),
                ("Lily", "The seeds can grow now."),
            ],
        },
        {
            "case_id": "extended-duration-default",
            "title": "九十秒默认预算",
            "target_duration_sec": 90,
            "coverage_tags": ["duration_default", "shot_count"],
            "characters": ["Tess", "Bo"],
            "locations": ["海边步道", "小码头", "沙滩"],
            "props": ["纸船", "蓝色丝带"],
            "dialogues": [
                ("Tess", "My boat keeps turning."),
                ("Bo", "The ribbon is pulling it."),
                ("Tess", "Can you hold the ribbon?"),
                ("Bo", "Yes, try it now."),
                ("Tess", "It goes straight!"),
            ],
        },
        {
            "case_id": "extended-duration-upper-boundary",
            "title": "三分钟时长预算上界",
            "target_duration_sec": 180,
            "coverage_tags": ["duration_boundary", "large_shot_plan"],
            "characters": ["Kai", "Rin", "Uma"],
            "locations": ["学校走廊", "图书馆", "操场", "失物招领处"],
            "props": ["红手套", "借书卡", "绿色背包"],
            "dialogues": [
                ("Kai", "My red glove is gone."),
                ("Rin", "Let us check the library."),
                ("Uma", "I saw it near the green bag."),
                ("Kai", "It is not in the bag."),
                ("Rin", "Look at these wet footprints."),
                ("Uma", "They go to the playground."),
                ("Kai", "There is my glove!"),
                ("Rin", "Please put both gloves away."),
            ],
        },
        {
            "case_id": "extended-noop-clean",
            "title": "正确草案不应被无意义改写",
            "target_duration_sec": 60,
            "coverage_tags": ["noop_preservation", "clean_case"],
            "characters": ["Nora", "Ben"],
            "locations": ["安静阅读角"],
            "props": ["图画书"],
            "dialogues": [
                ("Nora", "Can I sit here?"),
                ("Ben", "Yes, there is room."),
                ("Nora", "Thank you."),
            ],
        },
        {
            "case_id": "extended-regression-trap",
            "title": "修局部问题时保留既有事实",
            "target_duration_sec": 60,
            "coverage_tags": ["regression_trap", "protected_fact", "multi_must_fix_risk"],
            "characters": ["An", "Yu"],
            "locations": ["雨后公园", "凉亭"],
            "props": ["黄色雨衣", "纸飞机", "月亮钥匙扣"],
            "dialogues": [
                ("An", "The paper plane is under the bench."),
                ("Yu", "I can reach it."),
                ("An", "Please keep my moon keychain dry."),
                ("Yu", "It is safe in my pocket."),
            ],
        },
    ]
    return [_extra_storyboard_case(spec, index=index) for index, spec in enumerate(specs, start=1)]


def evaluate_storyboard(
    *,
    case: dict[str, Any],
    shots: list[dict[str, Any]] | None,
    parse_error: str | None = None,
) -> dict[str, Any]:
    source = ShotDirectionInput.from_payload(case["source"])
    gold = case.get("gold") if isinstance(case.get("gold"), dict) else {}
    values = deepcopy(shots) if isinstance(shots, list) else []

    used_dialogues: list[str] = [
        str(dialogue_id)
        for shot in values
        if isinstance(shot, dict)
        for dialogue_id in shot.get("dialogue_ids") or []
        if str(dialogue_id)
    ]
    required_dialogues = set(_string_list(gold.get("required_dialogue_ids")))
    covered_dialogues = required_dialogues.intersection(used_dialogues)

    used_references = {
        "character": {
            str(value)
            for shot in values
            if isinstance(shot, dict)
            for value in shot.get("character_ids") or []
            if str(value)
        },
        "scene": {
            str(shot.get("scene_id"))
            for shot in values
            if isinstance(shot, dict) and str(shot.get("scene_id") or "")
        },
        "prop": {
            str(value)
            for shot in values
            if isinstance(shot, dict)
            for value in shot.get("prop_ids") or []
            if str(value)
        },
    }
    required_references = {
        kind: set(_string_list((gold.get("required_references") or {}).get(kind)))
        for kind in ("character", "scene", "prop")
    }
    required_reference_total = sum(len(items) for items in required_references.values())
    correct_reference_total = sum(
        len(required_references[kind].intersection(used_references[kind]))
        for kind in required_references
    )

    used_source_scenes = {
        _positive_int((shot.get("shot_card") or {}).get("source_scene_no"), 0)
        for shot in values
        if isinstance(shot, dict)
    } - {0}
    required_source_scenes = {
        int(value)
        for value in gold.get("required_source_scene_nos") or []
        if _positive_int(value, 0)
    }

    contract = source.segment_contract
    min_count = int(contract.get("min_segment_count", 1))
    max_count = int(contract.get("max_segment_count", max(1, len(values))))
    shot_count_compliant = bool(values) and min_count <= len(values) <= max_count
    min_beats = int(contract.get("min_internal_shots", 2))
    max_beats = int(contract.get("max_internal_shots", 4))
    beat_count_compliant = bool(values) and all(
        min_beats
        <= len((shot.get("shot_card") or {}).get("beats") or [])
        <= max_beats
        for shot in values
        if isinstance(shot, dict)
    )

    shot_duration_range = contract.get("shot_duration_range") or {}
    min_duration = float(shot_duration_range.get("min_sec", 4))
    max_duration = float(shot_duration_range.get("max_sec", 15))
    durations: list[float] = []
    duration_values_valid = bool(values)
    for shot in values:
        try:
            duration = float(shot.get("duration_sec"))
        except (TypeError, ValueError):
            duration_values_valid = False
            continue
        durations.append(duration)
        if not min_duration <= duration <= max_duration:
            duration_values_valid = False
    total_duration_range = contract.get("total_duration_range") or {}
    min_total = float(total_duration_range.get("min_sec", 0))
    max_total = float(total_duration_range.get("max_sec", float("inf")))
    duration_compliant = (
        duration_values_valid
        and len(durations) == len(values)
        and min_total <= sum(durations) <= max_total
    )

    allowed_ids = {
        "character": source.allowed_character_ids,
        "scene": source.allowed_scene_ids,
        "prop": source.allowed_prop_ids,
        "dialogue": source.allowed_dialogue_ids,
    }
    invalid_references = {
        "character": sorted(used_references["character"] - allowed_ids["character"]),
        "scene": sorted(used_references["scene"] - allowed_ids["scene"]),
        "prop": sorted(used_references["prop"] - allowed_ids["prop"]),
        "dialogue": sorted(set(used_dialogues) - allowed_ids["dialogue"]),
    }
    reference_valid = not any(invalid_references.values())
    dialogue_unique = len(used_dialogues) == len(set(used_dialogues))
    dialogue_coverage_rate = (
        len(covered_dialogues) / len(required_dialogues)
        if required_dialogues
        else 1.0
    )
    entity_reference_accuracy = (
        correct_reference_total / required_reference_total
        if required_reference_total
        else 1.0
    )
    source_scene_coverage_rate = (
        len(required_source_scenes.intersection(used_source_scenes)) / len(required_source_scenes)
        if required_source_scenes
        else 1.0
    )

    contract_report: dict[str, Any]
    try:
        contract_report = build_shot_contract_report(
            shots=values,
            source=source,
            review_available=True,
        )
    except Exception as exc:  # benchmark must retain malformed samples
        contract_report = {
            "valid": False,
            "quality_gate": "evaluator_error",
            "errors": [{"code": "contract_evaluator_error", "message": str(exc)}],
            "warnings": [],
        }

    assertion_results = {
        "schema_valid": not parse_error and bool(values),
        "dialogue_coverage_complete": dialogue_coverage_rate == 1.0,
        "dialogue_unique": dialogue_unique,
        "entity_reference_complete": entity_reference_accuracy == 1.0,
        "reference_ids_valid": reference_valid,
        "source_scene_coverage_complete": source_scene_coverage_rate == 1.0,
        "shot_count_compliant": shot_count_compliant,
        "internal_beat_count_compliant": beat_count_compliant,
        "duration_compliant": duration_compliant,
        "production_contract_valid": bool(contract_report.get("valid")),
    }
    return {
        "evaluator_version": EVALUATOR_VERSION,
        "gate_passed": all(assertion_results.values()),
        "assertion_results": assertion_results,
        "dialogue_coverage_rate": round(dialogue_coverage_rate, 6),
        "entity_reference_accuracy": round(entity_reference_accuracy, 6),
        "source_scene_coverage_rate": round(source_scene_coverage_rate, 6),
        "required_dialogue_count": len(required_dialogues),
        "covered_dialogue_count": len(covered_dialogues),
        "required_reference_count": required_reference_total,
        "correct_reference_count": correct_reference_total,
        "shot_count": len(values),
        "shot_count_range": [min_count, max_count],
        "planned_duration_sec": round(sum(durations), 3),
        "duration_range_sec": [min_total, max_total],
        "missing_dialogue_ids": sorted(required_dialogues - covered_dialogues),
        "missing_references": {
            kind: sorted(required_references[kind] - used_references[kind])
            for kind in required_references
        },
        "missing_source_scene_nos": sorted(required_source_scenes - used_source_scenes),
        "invalid_references": invalid_references,
        "parse_error": parse_error,
        "contract_report": contract_report,
    }


def build_bounded_patch_cases(count: int = 30) -> list[dict[str, Any]]:
    defect_types = (
        "missing_dialogue",
        "duplicate_dialogue",
        "missing_scene_coverage",
        "duration_out_of_range",
        "internal_beat_count",
        "missing_storyboard_prompt",
        "visible_action_missing",
        "missing_entity_binding",
        "dialogue_wrong_shot",
        "continuity_fact",
    )
    cases: list[dict[str, Any]] = []
    for index in range(1, count + 1):
        defect_type = defect_types[(index - 1) % len(defect_types)]
        case_id = f"patch-{index:02d}-{defect_type}"
        source, clean = _bounded_patch_base(case_id)
        draft = deepcopy(clean)
        target = 2
        allowed_prefixes: list[str]
        must_fix: dict[str, Any]

        if defect_type == "missing_dialogue":
            missing_id = source.dialogues[-1]["id"]
            _remove_dialogue(draft, missing_id)
            must_fix = {"dialogue_id": missing_id}
            allowed_prefixes = [
                "shot[2].dialogue_ids",
                "shot[2].shot_card.beats",
                "shot[2].shot_card.motion_timing.beats",
            ]
        elif defect_type == "duplicate_dialogue":
            duplicate_id = source.dialogues[0]["id"]
            draft[1]["shot_card"]["beats"][0]["dialogue_ids"].append(duplicate_id)
            draft[1]["dialogue_ids"].append(duplicate_id)
            must_fix = {"dialogue_id": duplicate_id}
            allowed_prefixes = ["shot[2].dialogue_ids", "shot[2].shot_card.beats"]
        elif defect_type == "missing_scene_coverage":
            draft[1]["shot_card"]["source_scene_no"] = 1
            must_fix = {"source_scene_no": 2}
            allowed_prefixes = ["shot[2].shot_card.source_scene_no"]
        elif defect_type == "duration_out_of_range":
            draft[1]["duration_sec"] = 16
            draft[1]["shot_card"]["segment_plan"]["duration_sec"] = 16
            must_fix = {"duration_min": 4, "duration_max": 15}
            allowed_prefixes = [
                "shot[2].duration_sec",
                "shot[2].shot_card.duration_rationale",
                "shot[2].shot_card.segment_plan",
            ]
        elif defect_type == "internal_beat_count":
            draft[1]["shot_card"]["beats"] = draft[1]["shot_card"]["beats"][:1]
            draft[1]["shot_card"]["motion_timing"]["beats"] = deepcopy(
                draft[1]["shot_card"]["beats"]
            )
            draft[1]["dialogue_ids"] = list(
                draft[1]["shot_card"]["beats"][0]["dialogue_ids"]
            )
            must_fix = {"min_beats": 2, "max_beats": 4}
            allowed_prefixes = [
                "shot[2].dialogue_ids",
                "shot[2].shot_card.beats",
                "shot[2].shot_card.motion_timing.beats",
            ]
        elif defect_type == "missing_storyboard_prompt":
            draft[1]["image_prompts"]["shot_storyboard"]["positive_prompt"] = ""
            draft[1]["shot_card"]["image_prompts"]["shot_storyboard"]["positive_prompt"] = ""
            draft[1]["image_prompt"] = ""
            must_fix = {"positive_prompt_required": True}
            allowed_prefixes = [
                "shot[2].image_prompts.shot_storyboard.positive_prompt",
                "shot[2].image_prompt",
                "shot[2].shot_card.image_prompts.shot_storyboard.positive_prompt",
            ]
        elif defect_type == "visible_action_missing":
            draft[1]["description"] = ""
            draft[1]["shot_card"]["action"]["main_action"] = ""
            must_fix = {"visible_action_required": True}
            allowed_prefixes = ["shot[2].description", "shot[2].shot_card.action.main_action"]
        elif defect_type == "missing_entity_binding":
            required_id = source.props[-1]["id"]
            draft[1]["prop_ids"] = [value for value in draft[1]["prop_ids"] if value != required_id]
            draft[1]["shot_card"]["storyboard_frame"]["prop_ids"] = []
            must_fix = {"prop_id": required_id}
            allowed_prefixes = [
                "shot[2].prop_ids",
                "shot[2].shot_card.storyboard_frame.prop_ids",
                "shot[2].image_prompts.shot_storyboard.visible_prop_ids",
                "shot[2].shot_card.image_prompts.shot_storyboard.visible_prop_ids",
            ]
        elif defect_type == "dialogue_wrong_shot":
            dialogue_id = source.dialogues[-1]["id"]
            _remove_dialogue(draft, dialogue_id)
            draft[0]["shot_card"]["beats"][-1]["dialogue_ids"].append(dialogue_id)
            draft[0]["dialogue_ids"].append(dialogue_id)
            # The critic authorizes both the incorrect and correct location. This is
            # still bounded: every untouched shot remains protected.
            target = 2
            must_fix = {"dialogue_id": dialogue_id, "expected_shot_no": 2}
            allowed_prefixes = [
                "shot[1].dialogue_ids",
                "shot[1].shot_card.beats",
                "shot[1].shot_card.motion_timing.beats",
                "shot[2].dialogue_ids",
                "shot[2].shot_card.beats",
                "shot[2].shot_card.motion_timing.beats",
            ]
        else:  # continuity_fact
            draft[1]["shot_card"]["action"]["end_state"] = "红球突然回到桌上。"
            draft[1]["shot_card"]["frame_plan"]["last_frame"] = "红球突然回到桌上。"
            must_fix = {"required_text": "红球留在Leo手中"}
            allowed_prefixes = [
                "shot[2].shot_card.action.end_state",
                "shot[2].shot_card.frame_plan.last_frame",
            ]

        issue_code = f"benchmark_{defect_type}"
        allowed_shot_nos = [1, 2] if defect_type == "dialogue_wrong_shot" else [target]
        review = {
            "summary": "独立 gold critic 注入一个可验证的局部问题。",
            "issues": [
                {
                    "code": issue_code,
                    "category": _patch_category(defect_type),
                    "severity": "must_fix",
                    "message": _patch_issue_message(defect_type, must_fix),
                    "shot_nos": allowed_shot_nos,
                    "evidence": canonical_json(must_fix),
                    "suggestion": "只修复点名问题并保留其他字段、对白和实体引用。",
                }
            ],
        }
        cases.append(
            {
                "case_id": case_id,
                "case_type": "controlled_bounded_patch",
                "defect_type": defect_type,
                "source": source.to_payload(),
                "draft": draft,
                "clean_reference": clean,
                "review": review,
                "gold": {
                    "must_fix": must_fix,
                    "issue_code": issue_code,
                    "allowed_shot_nos": allowed_shot_nos,
                    "allowed_to_change": allowed_prefixes,
                    "must_preserve": [
                        f"all fields outside {', '.join(allowed_prefixes)}",
                        "all entity and Dialogue IDs not named by must_fix",
                        "all unreviewed shots",
                    ],
                },
            }
        )
    return cases


def evaluate_bounded_patch(
    *,
    case: dict[str, Any],
    final_shots: list[dict[str, Any]] | None,
    patch: dict[str, Any] | None,
    parse_error: str | None,
    raw_text: str,
) -> dict[str, Any]:
    draft = case["draft"]
    final = final_shots if isinstance(final_shots, list) else []
    gold = case["gold"]
    allowed_prefixes = _string_list(gold.get("allowed_to_change"))
    protected_opportunities = 0
    protected_edits: list[dict[str, Any]] = []
    allowed_edits: list[dict[str, Any]] = []

    max_shots = max(len(draft), len(final))
    for index in range(max_shots):
        before = _flatten_shot(draft[index], index + 1) if index < len(draft) else {}
        after = _flatten_shot(final[index], index + 1) if index < len(final) else {}
        for path in sorted(set(before) | set(after)):
            if _volatile_path(path):
                continue
            changed = before.get(path, _MISSING) != after.get(path, _MISSING)
            allowed = any(
                path == prefix
                or path.startswith(prefix + ".")
                or path.startswith(prefix + "[")
                for prefix in allowed_prefixes
            )
            if allowed:
                if changed:
                    allowed_edits.append({"path": path, "before": before.get(path), "after": after.get(path)})
                continue
            protected_opportunities += 1
            if changed:
                protected_edits.append(
                    {"path": path, "before": before.get(path), "after": after.get(path)}
                )

    target_fixed = _patch_target_fixed(case, final)
    issue_code = str(gold.get("issue_code") or "")
    declared_resolved = issue_code in _string_list((patch or {}).get("resolved_issue_codes"))
    operations = (patch or {}).get("operations") if isinstance(patch, dict) else []
    authorized_targets = set(int(value) for value in gold.get("allowed_shot_nos") or [])
    observed_targets = {
        _positive_int(item.get("shot_no"), 0)
        for item in operations or []
        if isinstance(item, dict)
    } - {0}
    boundary_authorized = observed_targets.issubset(authorized_targets)
    full_rewrite_attempt = (
        bool(re.search(r'"shots"\s*:', raw_text))
        and not bool(re.search(r'"shot_patch"\s*:', raw_text))
    ) or len(operations or []) > len(authorized_targets)
    regression = bool(target_fixed and protected_edits)
    return {
        "evaluator_version": PATCH_EVALUATOR_VERSION,
        "target_fix_success": bool(target_fixed),
        "declared_resolved": declared_resolved,
        "declared_but_unverified": bool(declared_resolved and not target_fixed),
        "protected_field_opportunities": protected_opportunities,
        "protected_field_edits": len(protected_edits),
        "protected_field_preservation_rate": round(
            (protected_opportunities - len(protected_edits)) / protected_opportunities,
            6,
        ) if protected_opportunities else 1.0,
        "out_of_bounds_edit_rate": round(
            len(protected_edits) / protected_opportunities,
            6,
        ) if protected_opportunities else 0.0,
        "boundary_authorized": boundary_authorized,
        "full_rewrite_attempt": full_rewrite_attempt,
        "regression": regression,
        "parse_valid": parse_error is None and bool(final),
        "parse_error": parse_error,
        "protected_edits": protected_edits,
        "allowed_edits": allowed_edits,
        "observed_target_shot_nos": sorted(observed_targets),
    }


def aggregate_rates(rows: list[dict[str, Any]], field: str) -> float:
    values = [row.get(field) for row in rows if row.get(field) is not None]
    if not values:
        return 0.0
    return sum(float(value) for value in values) / len(values)


def percentile(values: Iterable[float | int], p: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    rank = (len(ordered) - 1) * min(1.0, max(0.0, p))
    lower = int(rank)
    upper = min(len(ordered) - 1, lower + 1)
    fraction = rank - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def phase_token_usage(checkpoint: dict[str, Any] | None) -> dict[str, int]:
    totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    responses = checkpoint.get("responses") if isinstance(checkpoint, dict) else None
    if not isinstance(responses, dict):
        return totals
    for record in responses.values():
        if not isinstance(record, dict):
            continue
        raw = record.get("raw_response") if isinstance(record.get("raw_response"), dict) else {}
        provider_response = (
            raw.get("provider_response")
            if isinstance(raw.get("provider_response"), dict)
            else {}
        )
        usage = provider_response.get("usage") if isinstance(provider_response.get("usage"), dict) else {}
        for key in totals:
            try:
                totals[key] += int(usage.get(key) or 0)
            except (TypeError, ValueError):
                continue
    return totals


def compact_checkpoint_raw(checkpoint: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(checkpoint, dict):
        return {}
    return deepcopy(checkpoint)


def _shot_source_from_script(
    *,
    case_id: str,
    title: str,
    final: dict[str, Any],
    target_duration_sec: int,
) -> tuple[ShotDirectionInput, dict[str, Any]]:
    scenes = [deepcopy(item) for item in final.get("scenes") or [] if isinstance(item, dict)]
    character_names: list[str] = []
    for scene in scenes:
        for name in scene.get("characters") or []:
            _append_unique(character_names, str(name))
        for dialogue in scene.get("dialogues") or []:
            if isinstance(dialogue, dict):
                _append_unique(character_names, str(dialogue.get("speaker") or ""))
    if not character_names:
        character_names = ["Mia"]
    character_ids = {
        name: f"char-{_safe_id(case_id)}-{index}"
        for index, name in enumerate(character_names, start=1)
    }

    scene_names: list[str] = []
    for index, scene in enumerate(scenes, start=1):
        name = str(scene.get("location") or "").strip() or f"场景{index}"
        _append_unique(scene_names, name)
    scene_ids = {
        name: f"scene-{_safe_id(case_id)}-{index}"
        for index, name in enumerate(scene_names, start=1)
    }

    prop_names: list[str] = []
    for scene in scenes:
        for prop in scene.get("props") or []:
            _append_unique(prop_names, str(prop))
    prop_ids = {
        name: f"prop-{_safe_id(case_id)}-{index}"
        for index, name in enumerate(prop_names, start=1)
    }

    dialogue_rows: list[dict[str, Any]] = []
    sequence = 0
    for scene_index, scene in enumerate(scenes, start=1):
        scene_no = _positive_int(scene.get("scene_no"), scene_index)
        location = str(scene.get("location") or "").strip() or f"场景{scene_index}"
        scene["benchmark_scene_id"] = scene_ids[location]
        nested: list[dict[str, Any]] = []
        for dialogue in scene.get("dialogues") or []:
            if not isinstance(dialogue, dict) or not str(dialogue.get("text") or "").strip():
                continue
            sequence += 1
            item = deepcopy(dialogue)
            item["id"] = f"dlg-{_safe_id(case_id)}-{sequence}"
            item["scene_no"] = scene_no
            item["sequence_order"] = sequence - 1
            item["speaker"] = str(item.get("speaker") or character_names[0])
            item["speaker_name"] = item["speaker"]
            item["character_id"] = character_ids.get(item["speaker"])
            dialogue_rows.append(item)
            nested.append(deepcopy(item))
        scene["dialogues"] = nested

    source = ShotDirectionInput(
        project_id=f"storyboard-{_safe_id(case_id)}",
        script_id=f"script-{_safe_id(case_id)}",
        title=title,
        style="高品质风格化三维儿童动画",
        target_duration_sec=target_duration_sec,
        aspect_ratio="16:9",
        resolution="854x480",
        creative_settings={"english_level": "A1"},
        script_content=str(final.get("content") or render_readable_script(scenes)),
        script_scenes=scenes,
        dialogues=dialogue_rows,
        characters=[
            {
                "id": character_ids[name],
                "name": name,
                "identity": "剧本中实际出场或说话的角色",
                "appearance": "保持同一动画角色外观",
                "asset_spec": {},
            }
            for name in character_names
        ],
        scenes=[
            {
                "id": scene_ids[name],
                "name": name,
                "description": f"剧本中的{name}",
                "asset_spec": {},
            }
            for name in scene_names
        ],
        props=[
            {
                "id": prop_ids[name],
                "name": name,
                "description": f"剧本中参与动作的{name}",
                "story_function": "参与可见动作或因果",
                "asset_spec": {},
            }
            for name in prop_names
        ],
        adopted_assets=[],
        segment_contract=segment_planning_contract(target_duration_sec),
    )
    gold = {
        "required_dialogue_ids": [item["id"] for item in dialogue_rows],
        "required_references": {
            "character": sorted(character_ids.values()),
            "scene": sorted(scene_ids.values()),
            "prop": sorted(prop_ids.values()),
        },
        "required_source_scene_nos": sorted(source.source_scene_nos),
        "target_duration_sec": target_duration_sec,
        "segment_contract": deepcopy(source.segment_contract),
    }
    return source, gold


def _extra_storyboard_case(spec: dict[str, Any], *, index: int) -> dict[str, Any]:
    names = list(spec["characters"])
    locations = list(spec["locations"])
    props = list(spec["props"])
    dialogues = list(spec["dialogues"])
    scene_count = max(1, len(locations))
    script_scenes: list[dict[str, Any]] = []
    cursor = 0
    for scene_index in range(scene_count):
        remaining_scenes = scene_count - scene_index
        remaining_dialogues = len(dialogues) - cursor
        take = max(1, (remaining_dialogues + remaining_scenes - 1) // remaining_scenes)
        scene_dialogues = dialogues[cursor : cursor + take]
        cursor += take
        script_scenes.append(
            {
                "scene_no": scene_index + 1,
                "title": f"{spec['title']}·第{scene_index + 1}场",
                "location": locations[scene_index],
                "time_of_day": "白天",
                "characters": names,
                "props": props,
                "visible_action": f"角色在{locations[scene_index]}围绕{props[scene_index % len(props)]}完成一个有因果的可见步骤。",
                "story_purpose": "推进问题、尝试或结果。",
                "start_state": "本场目标尚未完成。",
                "end_state": "角色的行动产生了新状态。",
                "mood": "自然、专注",
                "source_evidence": "独立扩展 Benchmark 固定输入。",
                "inferred_elements": [],
                "sound_cues": ["与动作同步的轻声音"],
                "dialogues": [
                    {
                        "speaker": speaker,
                        "text": text,
                        "translation_zh": "",
                        "emotion": "自然交流",
                        "source_type": "created",
                        "sound_cues": [],
                    }
                    for speaker, text in scene_dialogues
                ],
            }
        )
    final = {"scenes": script_scenes, "content": render_readable_script(script_scenes)}
    source, gold = _shot_source_from_script(
        case_id=str(spec["case_id"]),
        title=str(spec["title"]),
        final=final,
        target_duration_sec=int(spec["target_duration_sec"]),
    )
    return {
        "case_id": str(spec["case_id"]),
        "case_type": "independent_storyboard_extension",
        "coverage_tags": list(spec.get("coverage_tags") or []),
        "source_origin": "hand_authored_fixed_benchmark",
        "source": source.to_payload(),
        "gold": gold,
        "dataset_order": 31 + index,
    }


def _fallback_script_for_eval_case(case: dict[str, Any]) -> dict[str, Any]:
    source = str(case.get("source_text") or case.get("outline") or case.get("title") or "").strip()
    required_phrases = _string_list(case.get("required_phrases"))
    required_tokens = _string_list(case.get("required_tokens"))
    characters = [token for token in required_tokens if re.fullmatch(r"[A-Z][A-Za-z0-9_-]*", token)]
    if not characters:
        characters = ["Mia", "Leo"]
    props = [token for token in required_tokens if token not in characters]
    dialogues = [
        {
            "speaker": characters[index % len(characters)],
            "text": phrase,
            "translation_zh": "",
            "emotion": "自然交流",
            "source_type": "source",
            "sound_cues": [],
        }
        for index, phrase in enumerate(required_phrases)
    ]
    if not dialogues:
        dialogues = [
            {
                "speaker": characters[0],
                "text": "Can you help me?",
                "translation_zh": "你能帮我吗？",
                "emotion": "请求",
                "source_type": "created",
                "sound_cues": [],
            },
            {
                "speaker": characters[-1],
                "text": "Yes, I can.",
                "translation_zh": "好的。",
                "emotion": "回应",
                "source_type": "created",
                "sound_cues": [],
            },
        ]
    visible = "；".join([source, *required_tokens]).strip("；") or "两名角色合作完成一个可见任务。"
    scenes = [
        {
            "scene_no": 1,
            "title": str(case.get("title") or "固定回退场景"),
            "location": "主要场景",
            "time_of_day": "白天",
            "characters": characters,
            "props": props,
            "visible_action": visible,
            "story_purpose": "保留原始输入并完成可见行动。",
            "start_state": "任务尚未完成。",
            "end_state": "任务由合作完成。",
            "mood": "自然",
            "source_evidence": source,
            "inferred_elements": [],
            "sound_cues": [],
            "dialogues": dialogues,
        }
    ]
    return {"scenes": scenes, "dialogues": dialogues, "content": render_readable_script(scenes)}


def _storyboard_duration_for_index(index: int) -> int:
    if index in {8, 16, 24, 31}:
        return 90
    if index in {10, 20, 30}:
        return 75
    return 60


def _bounded_patch_base(case_id: str) -> tuple[ShotDirectionInput, list[dict[str, Any]]]:
    prefix = _safe_id(case_id)
    source = ShotDirectionInput(
        project_id=f"project-{prefix}",
        script_id=f"script-{prefix}",
        title="红球交接",
        style="高品质风格化三维儿童动画",
        target_duration_sec=24,
        aspect_ratio="16:9",
        resolution="854x480",
        creative_settings={"english_level": "A1"},
        script_content="Mia把红球交给Leo，Leo接住并保管红球。",
        script_scenes=[
            {"scene_no": 1, "visible_action": "Mia拿起红球并走向Leo。"},
            {"scene_no": 2, "visible_action": "Leo接住红球，红球留在Leo手中。"},
        ],
        dialogues=[
            {"id": f"dlg-{prefix}-1", "speaker": "Mia", "text": "Can you catch it?"},
            {"id": f"dlg-{prefix}-2", "speaker": "Leo", "text": "Yes, I can!"},
            {"id": f"dlg-{prefix}-3", "speaker": "Mia", "text": "Keep it safe, please."},
            {"id": f"dlg-{prefix}-4", "speaker": "Leo", "text": "I will."},
        ],
        characters=[
            {"id": f"char-{prefix}-mia", "name": "Mia", "asset_spec": {}},
            {"id": f"char-{prefix}-leo", "name": "Leo", "asset_spec": {}},
        ],
        scenes=[
            {"id": f"scene-{prefix}-yard", "name": "院子", "asset_spec": {}},
            {"id": f"scene-{prefix}-bench", "name": "长椅旁", "asset_spec": {}},
        ],
        props=[
            {"id": f"prop-{prefix}-ball", "name": "红球", "asset_spec": {}},
            {"id": f"prop-{prefix}-bag", "name": "蓝色袋子", "asset_spec": {}},
        ],
        adopted_assets=[],
        segment_contract=segment_planning_contract(24),
    )
    shots = [
        _patch_shot(
            source,
            shot_no=1,
            scene_no=1,
            scene_id=source.scenes[0]["id"],
            dialogue_ids=[source.dialogues[0]["id"], source.dialogues[1]["id"]],
            prop_ids=[source.props[0]["id"]],
            description="Mia抛出红球，Leo伸手接住。",
            start_state="Mia双手拿着红球。",
            end_state="Leo刚接住红球。",
        ),
        _patch_shot(
            source,
            shot_no=2,
            scene_no=2,
            scene_id=source.scenes[1]["id"],
            dialogue_ids=[source.dialogues[2]["id"], source.dialogues[3]["id"]],
            prop_ids=[source.props[0]["id"], source.props[1]["id"]],
            description="Leo把红球放在胸前，答应保管好它。",
            start_state="Leo刚接住红球。",
            end_state="红球留在Leo手中，蓝色袋子仍在长椅上。",
        ),
    ]
    return source, shots


def _patch_shot(
    source: ShotDirectionInput,
    *,
    shot_no: int,
    scene_no: int,
    scene_id: str,
    dialogue_ids: list[str],
    prop_ids: list[str],
    description: str,
    start_state: str,
    end_state: str,
) -> dict[str, Any]:
    first = dialogue_ids[:1]
    rest = dialogue_ids[1:]
    storyboard = {
        "positive_prompt": f"院子中景，Mia和Leo围绕红球行动，{start_state}",
        "negative_prompt": "字幕，水印，logo，可读文字",
        "visible_character_ids": [item["id"] for item in source.characters],
        "visible_prop_ids": prop_ids,
    }
    beats = [
        {
            "beat_id": f"shot-{shot_no}-beat-1",
            "camera": "中景固定机位",
            "action": start_state,
            "dialogue_ids": first,
            "sound_cues": ["轻微衣料声"],
        },
        {
            "beat_id": f"shot-{shot_no}-beat-2",
            "camera": "近景轻微推进",
            "action": description,
            "dialogue_ids": rest,
            "sound_cues": ["红球轻响"],
        },
    ]
    return {
        "shot_no": shot_no,
        "description": description,
        "camera_shot": "中景",
        "camera_movement": "固定机位",
        "duration_sec": 12,
        "scene_id": scene_id,
        "character_ids": [item["id"] for item in source.characters],
        "prop_ids": prop_ids,
        "dialogue_ids": dialogue_ids,
        "video_prompt": "",
        "generation_mode": "image_to_video",
        "shot_card": {
            "schema_version": 3,
            "source_scene_no": scene_no,
            "story_purpose": "完成红球交接并保持状态连续。",
            "emotional_intent": "信任",
            "duration_rationale": "完整承载一次交接动作和自然回应。",
            "camera": {"shot_size": "中景", "angle": "平视", "movement": "固定机位"},
            "action": {"start_state": start_state, "main_action": description, "end_state": end_state},
            "frame_plan": {"first_frame": start_state, "key_frame": description, "last_frame": end_state},
            "storyboard_frame": {
                "description": start_state,
                "narrative_focus": "红球交接",
                "character_ids": [item["id"] for item in source.characters],
                "prop_ids": prop_ids,
            },
            "beats": beats,
            "motion_timing": {"motion_complexity": "低", "beats": deepcopy(beats)},
            "risk_flags": [],
            "review_checklist": ["红球持有关系连续", "Dialogue ID 不重复"],
            "segment_plan": {
                "duration_mode": "fixed",
                "duration_source": "agent",
                "duration_sec": 12,
                "duration_rationale": "完整承载一次交接动作和自然回应。",
                "internal_shot_count": 2,
            },
            "image_prompts": {"shot_storyboard": deepcopy(storyboard)},
        },
        "image_prompts": {"shot_storyboard": deepcopy(storyboard)},
        "image_prompt": storyboard["positive_prompt"],
        "negative_prompt": storyboard["negative_prompt"],
    }


def _patch_target_fixed(case: dict[str, Any], final: list[dict[str, Any]]) -> bool:
    if not final:
        return False
    defect = str(case.get("defect_type") or "")
    must_fix = case["gold"]["must_fix"]
    all_dialogues = [
        str(value)
        for shot in final
        for value in shot.get("dialogue_ids") or []
    ]
    beat_dialogues = [
        str(value)
        for shot in final
        for beat in (shot.get("shot_card") or {}).get("beats") or []
        if isinstance(beat, dict)
        for value in beat.get("dialogue_ids") or []
    ]
    motion_dialogues = [
        str(value)
        for shot in final
        for beat in ((shot.get("shot_card") or {}).get("motion_timing") or {}).get("beats") or []
        if isinstance(beat, dict)
        for value in beat.get("dialogue_ids") or []
    ]
    target_shot = final[1] if len(final) > 1 else {}
    if defect == "missing_dialogue":
        dialogue_id = str(must_fix["dialogue_id"])
        return all(
            values.count(dialogue_id) == 1
            for values in (all_dialogues, beat_dialogues, motion_dialogues)
        )
    if defect == "duplicate_dialogue":
        dialogue_id = str(must_fix["dialogue_id"])
        return all(
            values.count(dialogue_id) == 1
            for values in (all_dialogues, beat_dialogues, motion_dialogues)
        )
    if defect == "missing_scene_coverage":
        return any(
            _positive_int((shot.get("shot_card") or {}).get("source_scene_no"), 0)
            == int(must_fix["source_scene_no"])
            for shot in final
        )
    if defect == "duration_out_of_range":
        try:
            duration = float(target_shot.get("duration_sec"))
        except (TypeError, ValueError):
            return False
        total = sum(float(shot.get("duration_sec") or 0) for shot in final)
        segment_duration = ((target_shot.get("shot_card") or {}).get("segment_plan") or {}).get("duration_sec")
        try:
            segment_duration_value = float(segment_duration)
        except (TypeError, ValueError):
            return False
        return (
            float(must_fix["duration_min"]) <= duration <= float(must_fix["duration_max"])
            and 20.4 <= total <= 27.6
            and abs(segment_duration_value - duration) < 0.001
        )
    if defect == "internal_beat_count":
        count = len((target_shot.get("shot_card") or {}).get("beats") or [])
        timing_count = len(
            (((target_shot.get("shot_card") or {}).get("motion_timing") or {}).get("beats") or [])
        )
        return all(
            int(must_fix["min_beats"]) <= value <= int(must_fix["max_beats"])
            for value in (count, timing_count)
        )
    if defect == "missing_storyboard_prompt":
        prompts = [
            (((target_shot.get("image_prompts") or {}).get("shot_storyboard") or {}).get("positive_prompt")),
            ((((target_shot.get("shot_card") or {}).get("image_prompts") or {}).get("shot_storyboard") or {}).get("positive_prompt")),
            target_shot.get("image_prompt"),
        ]
        return all(bool(str(prompt or "").strip()) for prompt in prompts)
    if defect == "visible_action_missing":
        action = (target_shot.get("shot_card") or {}).get("action") or {}
        return bool(str(target_shot.get("description") or "").strip() and str(action.get("main_action") or "").strip())
    if defect == "missing_entity_binding":
        prop_id = str(must_fix["prop_id"])
        references = [
            target_shot.get("prop_ids") or [],
            ((target_shot.get("shot_card") or {}).get("storyboard_frame") or {}).get("prop_ids") or [],
            ((target_shot.get("image_prompts") or {}).get("shot_storyboard") or {}).get("visible_prop_ids") or [],
            ((((target_shot.get("shot_card") or {}).get("image_prompts") or {}).get("shot_storyboard") or {}).get("visible_prop_ids") or []),
        ]
        return all(prop_id in set(values) for values in references)
    if defect == "dialogue_wrong_shot":
        expected_no = int(must_fix["expected_shot_no"])
        dialogue_id = str(must_fix["dialogue_id"])
        expected = next(
            (shot for shot in final if int(shot.get("shot_no") or 0) == expected_no),
            None,
        )
        if expected is None or not all(
            values.count(dialogue_id) == 1
            for values in (all_dialogues, beat_dialogues, motion_dialogues)
        ):
            return False
        expected_beat_ids = [
            str(value)
            for beat in (expected.get("shot_card") or {}).get("beats") or []
            if isinstance(beat, dict)
            for value in beat.get("dialogue_ids") or []
        ]
        expected_motion_ids = [
            str(value)
            for beat in (((expected.get("shot_card") or {}).get("motion_timing") or {}).get("beats") or [])
            if isinstance(beat, dict)
            for value in beat.get("dialogue_ids") or []
        ]
        return (
            dialogue_id in set(expected.get("dialogue_ids") or [])
            and dialogue_id in expected_beat_ids
            and dialogue_id in expected_motion_ids
        )
    if defect == "continuity_fact":
        required = str(must_fix["required_text"])
        card = target_shot.get("shot_card") or {}
        return (
            required in str((card.get("action") or {}).get("end_state") or "")
            and required in str((card.get("frame_plan") or {}).get("last_frame") or "")
        )
    return False


def _patch_category(defect_type: str) -> str:
    if "dialogue" in defect_type:
        return "dialogue"
    if defect_type in {"missing_scene_coverage", "continuity_fact"}:
        return "continuity"
    if defect_type == "missing_entity_binding":
        return "asset_feasibility"
    return "production"


def _patch_issue_message(defect_type: str, must_fix: dict[str, Any]) -> str:
    return {
        "missing_dialogue": f"片段 2 遗漏 Dialogue {must_fix.get('dialogue_id')}。",
        "duplicate_dialogue": f"Dialogue {must_fix.get('dialogue_id')} 被重复绑定。",
        "missing_scene_coverage": "第 2 个剧本场次未被覆盖。",
        "duration_out_of_range": "片段 2 的时长超过单片段硬上限。",
        "internal_beat_count": "片段 2 只有一个内部镜头。",
        "missing_storyboard_prompt": "片段 2 的分镜图主体 Prompt 为空。",
        "visible_action_missing": "片段 2 缺少可见主动作。",
        "missing_entity_binding": f"片段 2 遗漏剧情必需道具 {must_fix.get('prop_id')}。",
        "dialogue_wrong_shot": f"Dialogue {must_fix.get('dialogue_id')} 绑定到错误片段。",
        "continuity_fact": "片段 2 破坏了红球持有关系。",
    }[defect_type]


def _remove_dialogue(shots: list[dict[str, Any]], dialogue_id: str) -> None:
    for shot in shots:
        shot["dialogue_ids"] = [value for value in shot.get("dialogue_ids") or [] if value != dialogue_id]
        card = shot.get("shot_card") if isinstance(shot.get("shot_card"), dict) else {}
        for key in ("beats",):
            for beat in card.get(key) or []:
                if isinstance(beat, dict):
                    beat["dialogue_ids"] = [
                        value for value in beat.get("dialogue_ids") or [] if value != dialogue_id
                    ]
        timing = card.get("motion_timing") if isinstance(card.get("motion_timing"), dict) else {}
        for beat in timing.get("beats") or []:
            if isinstance(beat, dict):
                beat["dialogue_ids"] = [
                    value for value in beat.get("dialogue_ids") or [] if value != dialogue_id
                ]


def _flatten_shot(shot: dict[str, Any], shot_no: int) -> dict[str, Any]:
    result: dict[str, Any] = {}

    def visit(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key in sorted(value):
                visit(value[key], f"{path}.{key}")
            return
        if isinstance(value, list):
            if all(not isinstance(item, (dict, list)) for item in value):
                result[path] = canonical_json(value)
                return
            for index, item in enumerate(value):
                visit(item, f"{path}[{index}]")
            return
        result[path] = value

    visit(shot, f"shot[{shot_no}]")
    return result


def _volatile_path(path: str) -> bool:
    return path.endswith(".shot_no") or path.endswith(".beat_id")


def _append_unique(values: list[str], value: str) -> None:
    normalized = value.strip()
    if normalized and normalized not in values:
        values.append(normalized)


def _safe_id(value: object) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
    return normalized[:48] or sha256(str(value).encode("utf-8")).hexdigest()[:12]


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _positive_int(value: Any, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def _csv_value(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple, set)):
        return canonical_json(value)
    if isinstance(value, Decimal):
        return format(value, "f")
    return value


_MISSING = object()
