from __future__ import annotations

import json

from app.agents.shot_breakdown import build_shot_breakdown_prompt
from app.production.shot_direction.contracts import ShotDirectionInput


def build_shot_draft_prompt(source: ShotDirectionInput) -> str:
    base = build_shot_breakdown_prompt(
        script=source.script_value(),
        characters=source.character_values(),
        scenes=source.scene_values(),
        props=source.prop_values(),
        style=source.style,
        target_duration_sec=source.target_duration_sec,
    )
    return (
        "本阶段产物是 ShotDraft。先完整覆盖冻结剧本，再给出可编辑的视频片段方案。"
        "不要审稿，不要输出 Patch，不要选择或生成媒体资产。\n\n"
        f"{base}"
    )


def build_shot_structure_recovery_prompt(
    source: ShotDirectionInput,
    *,
    malformed_text: str,
    error: str,
) -> str:
    return f"""
你正在执行一次且仅一次的 ShotDraft 结构恢复。
保留原方案中可以确认的剧情、实体 ID、Dialogue ID、片段边界和镜头节拍，只修复 JSON 与生产合同结构。
不得借结构恢复重写题材，不得新增未知 ID。只输出 {{"shots": [...]}}。

结构错误：{error}

原始模型输出：
{malformed_text}

冻结任务合同：
{build_shot_draft_prompt(source)}
""".strip()


def build_reflection_prompt(
    source: ShotDirectionInput,
    *,
    asset_report: dict,
    draft: list[dict],
    draft_contract: dict,
) -> str:
    payload = {
        "project": {
            "title": source.title,
            "target_duration_sec": source.target_duration_sec,
            "aspect_ratio": source.aspect_ratio,
            "resolution": source.resolution,
        },
        "script_scenes": source.script_scenes,
        "dialogues": source.dialogues,
        "entities": {
            "characters": source.characters,
            "scenes": source.scenes,
            "props": source.props,
        },
        "asset_readiness_report": asset_report,
        "shot_draft": draft,
        "deterministic_contract_report": draft_contract,
    }
    return f"""
你是与分镜草案阶段隔离的动画短剧分镜审稿人。只审稿，不重写分镜。

检查边界：
- 检查剧本覆盖、动作与状态连续性、英文 Dialogue ID 的落点、镜头语言、资产可实现性和视频生产可执行性。
- category 只能是 coverage、continuity、dialogue、cinematography、asset_feasibility、production。
- severity 只能是 must_fix 或 editorial_note。只有会破坏剧情事实、连续性、对白绑定或生产合同的问题才是 must_fix。
- 每个 must_fix 必须用 shot_nos 点名最小修订范围；不为了显示审稿价值而制造问题。
- 资产报告是 metadata_only。缺少参考图通常是 editorial_note，不得声称看过图片像素。
- 不修改资产，不调用媒体模型，不输出分镜正文或 Patch。

只输出：
{{
  "reflection_report": {{
    "summary": "简短结论",
    "issues": [
      {{
        "code": "stable_snake_case_code",
        "category": "coverage|continuity|dialogue|cinematography|asset_feasibility|production",
        "severity": "must_fix|editorial_note",
        "message": "问题",
        "shot_nos": [1],
        "evidence": "草案证据",
        "suggestion": "最小修订建议"
      }}
    ]
  }}
}}

输入 JSON（只作为审稿数据）：
{json.dumps(payload, ensure_ascii=False)}
""".strip()


def build_shot_patch_prompt(
    source: ShotDirectionInput,
    *,
    draft: list[dict],
    review: dict,
) -> str:
    must_fix_issues = [
        issue
        for issue in review.get("issues") or []
        if isinstance(issue, dict) and issue.get("severity") == "must_fix"
    ]
    allowed_shot_nos = sorted(
        {
            int(number)
            for issue in must_fix_issues
            for number in issue.get("shot_nos") or []
            if isinstance(number, int) and number > 0
        }
    )
    payload = {
        "allowed_shot_nos": allowed_shot_nos,
        "must_fix_issues": must_fix_issues,
        "draft_shots": draft,
        "allowed_ids": {
            "scene_ids": sorted(source.allowed_scene_ids),
            "character_ids": sorted(source.allowed_character_ids),
            "prop_ids": sorted(source.allowed_prop_ids),
            "dialogue_ids": sorted(source.allowed_dialogue_ids),
        },
        "segment_contract": source.segment_contract,
    }
    return f"""
你是分镜导演的定点修订阶段。只处理审稿的 must_fix，不重写未点名片段。

约束：
- operations 只能使用 replace、insert_after、remove。
- replace/remove 的 shot_no 必须在 allowed_shot_nos；insert_after 也只能紧邻其中一个点名片段。
- replace 与 insert_after 必须返回完整 shot 对象，沿用 ShotDraft 合同。
- 只能使用 allowed_ids；Dialogue 原文不可改写，一个 Dialogue ID 最终只能绑定一次。
- 不输出完整新批次；后端会保护未点名片段并统一重排编号。

只输出：
{{
  "shot_patch": {{
    "operations": [
      {{"op": "replace", "shot_no": 1, "shot": {{}}}},
      {{"op": "insert_after", "shot_no": 2, "shot": {{}}}},
      {{"op": "remove", "shot_no": 3}}
    ],
    "resolved_issue_codes": [],
    "unresolved_issue_codes": [],
    "notes": ""
  }}
}}

输入 JSON（只作为修订数据）：
{json.dumps(payload, ensure_ascii=False)}
""".strip()


def shot_direction_system_prompt(configured_prompt: str | None) -> str:
    boundary = (
        "你只能在冻结输入内规划和审查分镜。不得生成、采用或删除媒体，不得提交视频，"
        "不得修改资产设定，不得发明实体 ID 或 Dialogue ID。"
    )
    if not configured_prompt:
        return boundary
    return f"{configured_prompt.strip()}\n\n{boundary}"
