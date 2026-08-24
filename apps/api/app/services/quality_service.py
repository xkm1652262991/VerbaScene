from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import Asset, Project, QualityCheck, Shot
from app.agents.avd_asset_strategy import avd_asset_metadata
from app.agents.avd_rule_pack import AVD_RULE_PACK_VERSION, issues_to_dicts, scan_avd_prompt
from app.providers.defaults import provider_registry
from app.providers.types import ProviderType
from app.services.asset_repository import list_current_assets
from app.services.image_consistency_service import evaluate_image_consistency


QUALITY_METHOD = "rule"
SUPPORTED_STAGES = {"shots", "images"}


def get_project_or_404(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


def list_quality_checks(db: Session, project_id: str, stage: str | None = None) -> list[QualityCheck]:
    get_project_or_404(db, project_id)
    statement = select(QualityCheck).where(QualityCheck.project_id == project_id)
    if stage:
        statement = statement.where(QualityCheck.stage == stage)
    return list(
        db.scalars(
            statement.order_by(QualityCheck.stage, QualityCheck.target_type, QualityCheck.created_at.desc()),
        ).all()
    )


def list_quality_checks_page(
    db: Session,
    project_id: str,
    stage: str | None = None,
    offset: int = 0,
    limit: int = 500,
) -> tuple[list[QualityCheck], int]:
    get_project_or_404(db, project_id)
    statement = select(QualityCheck).where(QualityCheck.project_id == project_id)
    count_statement = select(func.count(QualityCheck.id)).where(QualityCheck.project_id == project_id)
    if stage:
        statement = statement.where(QualityCheck.stage == stage)
        count_statement = count_statement.where(QualityCheck.stage == stage)
    total = db.scalar(count_statement) or 0
    checks = list(
        db.scalars(
            statement.order_by(QualityCheck.stage, QualityCheck.target_type, QualityCheck.created_at.desc())
            .offset(offset)
            .limit(limit),
        ).all()
    )
    return checks, total


def run_quality_checks(db: Session, project_id: str, stage: str = "shots") -> list[QualityCheck]:
    get_project_or_404(db, project_id)
    if stage not in SUPPORTED_STAGES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported quality check stage: {stage}",
        )

    shots = _list_shots(db, project_id)
    if not shots:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Shots are required before quality checks",
        )

    assets = _list_assets(db, project_id)
    check_payloads = _project_stage_checks(stage, shots, assets)
    if stage == "shots":
        check_payloads.extend(_shot_checks(shots))
    elif stage == "images":
        check_payloads.extend(_image_checks(shots, assets))

    db.execute(
        delete(QualityCheck)
        .where(QualityCheck.project_id == project_id)
        .where(QualityCheck.stage == stage)
        .where(QualityCheck.method == QUALITY_METHOD)
    )
    checks = [QualityCheck(project_id=project_id, method=QUALITY_METHOD, **payload) for payload in check_payloads]
    db.add_all(checks)
    db.commit()
    return list_quality_checks(db, project_id, stage=stage)


def _list_shots(db: Session, project_id: str) -> list[Shot]:
    return list(
        db.scalars(
            select(Shot)
            .where(Shot.project_id == project_id)
            .where(Shot.is_current.is_(True))
            .order_by(Shot.shot_no)
        ).all()
    )


def _list_assets(db: Session, project_id: str) -> list[Asset]:
    return list_current_assets(db, project_id)


def _project_stage_checks(stage: str, shots: list[Shot], assets: list[Asset]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    suggestions: list[str] = []
    if stage == "shots":
        if not all(shot.status == "approved" for shot in shots):
            issues.append(_issue("warning", "unapproved_shots", "存在未确认分镜"))
            suggestions.append("先确认分镜表，再进入图片生成。")
    if stage == "images":
        selected_images = _selected_assets(assets, "image", "shot", "shot_storyboard")
        missing = [shot for shot in shots if shot.id not in selected_images]
        if missing:
            issues.append(_issue("error", "missing_selected_shot_images", f"{len(missing)} 个镜头缺少已选分镜图"))
            suggestions.append("为缺失镜头生成或上传分镜图，并设为当前版本。")
    return [
        _check_payload(
            stage=stage,
            target_type="project",
            target_id=None,
            issues=issues,
            suggestions=suggestions,
            pass_summary=f"{_stage_label(stage)}基础覆盖检查通过",
            fail_summary=f"{_stage_label(stage)}存在覆盖或确认问题",
        )
    ]


def _shot_checks(shots: list[Shot]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    seen_numbers: set[int] = set()
    for shot in shots:
        issues: list[dict[str, Any]] = []
        suggestions: list[str] = []
        if shot.shot_no in seen_numbers:
            issues.append(_issue("error", "duplicate_shot_no", f"镜头编号 {shot.shot_no} 重复"))
            suggestions.append("调整镜头编号，确保合成顺序唯一。")
        seen_numbers.add(shot.shot_no)
        if not shot.description.strip():
            issues.append(_issue("error", "missing_description", "镜头描述为空"))
            suggestions.append("补充镜头描述。")
        if not _text(shot.image_prompt):
            issues.append(_issue("error", "missing_image_prompt", "缺少图像 Prompt"))
            suggestions.append("重新生成分镜或手动补充图像 Prompt。")
        if not _text(shot.video_prompt):
            issues.append(_issue("error", "missing_video_prompt", "缺少视频 Prompt"))
            suggestions.append("重新生成分镜或手动补充视频 Prompt。")
        prompt_issues = scan_avd_prompt(shot.video_prompt, prompt_type="video")
        if prompt_issues:
            issues.extend(issues_to_dicts(prompt_issues))
            suggestions.append("按 AVD 规则修正 Prompt：可读文字改为图标/色块/抽象纹理，复杂运镜改为物理可执行镜头。")
        if shot.duration_sec is None:
            issues.append(_issue("warning", "missing_duration", "缺少镜头时长"))
        elif Decimal(shot.duration_sec) > Decimal("12"):
            issues.append(_issue("warning", "long_duration", "镜头时长超过 12 秒，图生视频失败风险较高"))
            suggestions.append("考虑拆成两个更短镜头，或在视频生成失败时优先拆分。")

        checks.append(
            _check_payload(
                stage="shots",
                target_type="shot",
                target_id=shot.id,
                shot_id=shot.id,
                issues=issues,
                suggestions=suggestions,
                pass_summary=f"镜头 {shot.shot_no} 分镜质检通过",
                fail_summary=f"镜头 {shot.shot_no} 需要处理 {len(issues)} 项问题",
            )
        )
    return checks


def _image_checks(shots: list[Shot], assets: list[Asset]) -> list[dict[str, Any]]:
    selected_images = _selected_assets(assets, "image", "shot", "shot_storyboard")
    checks = []
    for shot in shots:
        asset = selected_images.get(shot.id)
        issues: list[dict[str, Any]] = []
        suggestions: list[str] = []
        if asset is None:
            issues.append(_issue("error", "missing_selected_image", "缺少已选分镜图"))
            suggestions.append("生成或上传该镜头分镜图，并设为当前版本。")
        else:
            issues.extend(_asset_metadata_issues(asset, expected_type="image"))
            purpose_issues = _avd_video_reference_purpose_issues(asset)
            if purpose_issues:
                issues.extend(purpose_issues)
                suggestions.append("将展示类图片派生成无文字、无边框、无 UI 的 clean video_asset 后再用于视频参考。")
            if asset.width and asset.height:
                ratio = asset.width / asset.height
                if abs(ratio - (16 / 9)) > 0.08:
                    issues.append(_issue("warning", "aspect_ratio_mismatch", "分镜图比例偏离 16:9"))
                    suggestions.append("重生成分镜图或上传 16:9 图片。")
            if not _text(asset.prompt):
                issues.append(_issue("warning", "missing_asset_prompt", "资产缺少生成 Prompt 记录"))
            consistency_issues = _image_consistency_issues(asset)
            if consistency_issues:
                issues.extend(consistency_issues)
                suggestions.append("根据一致性评分重生成候选，优先选择角色/场景参考相似度更高的图片。")
        checks.append(
            _check_payload(
                stage="images",
                target_type="shot_image",
                target_id=asset.id if asset else shot.id,
                shot_id=shot.id,
                asset_id=asset.id if asset else None,
                issues=issues,
                suggestions=suggestions,
                pass_summary=f"镜头 {shot.shot_no} 分镜图规则质检通过",
                fail_summary=f"镜头 {shot.shot_no} 分镜图需要处理 {len(issues)} 项问题",
            )
        )
    return checks


def _selected_assets(assets: list[Asset], asset_type: str, entity_type: str, asset_role: str) -> dict[str, Asset]:
    selected: dict[str, Asset] = {}
    for asset in assets:
        if (
            asset.asset_type == asset_type
            and asset.entity_type == entity_type
            and asset.asset_role == asset_role
            and asset.entity_id
            and asset.is_selected
            and asset.status in {"ready_for_review", "approved"}
        ):
            selected[asset.entity_id] = asset
    return selected


def _asset_metadata_issues(asset: Asset, *, expected_type: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if asset.asset_type != expected_type:
        issues.append(_issue("error", "asset_type_mismatch", f"资产类型不是 {expected_type}"))
    if not asset.uri:
        issues.append(_issue("error", "missing_asset_uri", "资产 URI 为空"))
    if asset.status not in {"ready_for_review", "approved"}:
        issues.append(_issue("warning", "asset_not_reviewable", "资产状态不可审核"))
    if expected_type == "image" and (not asset.width or not asset.height):
        issues.append(_issue("warning", "missing_image_size", "图片缺少宽高元数据"))
    if expected_type == "video" and not asset.duration_sec:
        issues.append(_issue("warning", "missing_video_duration", "视频缺少时长元数据"))
    return issues


def _avd_video_reference_purpose_issues(asset: Asset) -> list[dict[str, Any]]:
    usage = avd_asset_metadata(
        asset_type=asset.asset_type,
        asset_role=asset.asset_role,
        entity_type=asset.entity_type,
        raw_response=asset.raw_response,
    )
    if usage["can_use_as_video_reference"]:
        return []
    return [
        _issue(
            "error",
            "avd_asset_not_video_reference",
            f"当前图片用途为 {usage['asset_purpose']}，不适合直接作为视频参考图",
        )
    ]


def _image_consistency_issues(asset: Asset) -> list[dict[str, Any]]:
    raw_response = asset.raw_response if isinstance(asset.raw_response, dict) else {}
    consistency = raw_response.get("consistency_check")
    request_metadata = raw_response.get("request_metadata") if isinstance(raw_response.get("request_metadata"), dict) else {}
    asset_resolution = request_metadata.get("asset_resolution") if isinstance(request_metadata, dict) else None
    asset_resolution = _asset_resolution_with_provider(asset, asset_resolution)
    has_references = bool(
        isinstance(asset_resolution, dict)
        and isinstance(asset_resolution.get("reference_assets"), list)
        and asset_resolution.get("reference_assets")
    )
    if has_references and (not isinstance(consistency, dict) or _consistency_needs_provider_refresh(consistency)):
        consistency = evaluate_image_consistency(image_uri=asset.uri, asset_resolution=asset_resolution)
    elif not isinstance(consistency, dict):
        return []
    issues: list[dict[str, Any]] = []
    for issue in consistency.get("issues") or []:
        if isinstance(issue, dict):
            issues.append(
                _issue(
                    str(issue.get("severity") or "warning"),
                    str(issue.get("code") or "image_consistency_warning"),
                    str(issue.get("message") or "图片一致性检查发现风险"),
                )
            )
    score = consistency.get("score")
    try:
        numeric_score = Decimal(str(score))
    except Exception:
        numeric_score = None
    if numeric_score is not None and numeric_score < Decimal("70"):
        issues.append(_issue("warning", "low_image_consistency_score", f"图片一致性评分偏低：{numeric_score}"))
    return issues


def _asset_resolution_with_provider(asset: Asset, asset_resolution: Any) -> dict[str, Any] | None:
    if not isinstance(asset_resolution, dict):
        return None
    payload = dict(asset_resolution)
    provider_payload = payload.get("provider")
    if isinstance(provider_payload, dict) and provider_payload.get("capabilities"):
        return payload
    capabilities: list[str] = []
    if asset.provider:
        try:
            provider = provider_registry.get(ProviderType.IMAGE, asset.provider)
            capabilities = list(provider.capabilities)
        except Exception:
            capabilities = []
    payload["provider"] = {
        "name": asset.provider,
        "model": asset.model,
        "capabilities": capabilities,
    }
    return payload


def _consistency_needs_provider_refresh(consistency: dict[str, Any]) -> bool:
    checks = consistency.get("checks")
    if not isinstance(checks, list):
        return True
    return not any(
        isinstance(check, dict) and check.get("name") == "provider_identity_reference_capability"
        for check in checks
    )


def _asset_resolution_shot_id(asset: Asset) -> str | None:
    raw_response = asset.raw_response if isinstance(asset.raw_response, dict) else {}
    request_metadata = raw_response.get("request_metadata") if isinstance(raw_response.get("request_metadata"), dict) else {}
    asset_resolution = request_metadata.get("asset_resolution") if isinstance(request_metadata, dict) else None
    if isinstance(asset_resolution, dict) and isinstance(asset_resolution.get("shot_id"), str):
        return asset_resolution["shot_id"]
    return None


def _check_payload(
    *,
    stage: str,
    target_type: str,
    target_id: str | None,
    issues: list[dict[str, Any]],
    suggestions: list[str],
    pass_summary: str,
    fail_summary: str,
    shot_id: str | None = None,
    asset_id: str | None = None,
) -> dict[str, Any]:
    severity = {issue.get("severity") for issue in issues}
    if "error" in severity:
        status_value = "fail"
    elif "warning" in severity:
        status_value = "warning"
    else:
        status_value = "pass"
    return {
        "stage": stage,
        "target_type": target_type,
        "target_id": target_id,
        "shot_id": shot_id,
        "asset_id": asset_id,
        "status": status_value,
        "score": _score(issues),
        "summary": pass_summary if status_value == "pass" else fail_summary,
        "issues": issues,
        "suggestions": list(dict.fromkeys(suggestions)),
        "raw_response": {"engine": "rule_quality_checker_v2", "rule_pack": AVD_RULE_PACK_VERSION},
    }


def _issue(severity: str, code: str, message: str) -> dict[str, Any]:
    return {"severity": severity, "code": code, "message": message}


def _score(issues: list[dict[str, Any]]) -> Decimal:
    score = Decimal("100")
    for issue in issues:
        score -= Decimal("25") if issue.get("severity") == "error" else Decimal("10")
    return max(score, Decimal("0"))


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _stage_label(stage: str) -> str:
    labels = {"shots": "分镜", "images": "图片"}
    return labels.get(stage, stage)
