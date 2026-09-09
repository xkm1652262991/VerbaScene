from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.agents import shot_video_params, shot_video_prompt
from app.core.config import settings
from app.models import Asset, Character, Prop, Scene, Shot
from app.providers.base import ProviderAdapter
from app.providers.defaults import provider_registry
from app.providers.types import ProviderType
from app.services.asset_repository import resolve_shot_video_input_asset
from app.services.asset_resolver_service import resolve_generation_assets
from app.services.entity_service import get_current_entities
from app.services.media_generation_support import with_avd_asset_usage


def compile_video_candidate_task_input(
    db: Session,
    shot: Shot,
    *,
    duration_mode: str | None = None,
    duration_sec: Decimal | None = None,
    video_prompt: str | None = None,
    source_asset: Asset | None = None,
    update_shot: bool = False,
    provider_name: str | None = None,
    model: str | None = None,
) -> tuple[dict, ProviderAdapter]:
    """Freeze every provider-relevant value before the task is queued."""
    provider = provider_registry.get(
        ProviderType.VIDEO,
        provider_name or settings.video_provider,
    )
    resolved_model = model or provider.model
    resolved_duration_mode, resolved_duration = resolve_video_duration_request(
        provider,
        shot,
        duration_mode=duration_mode,
        duration_sec=duration_sec,
    )
    if update_shot:
        if resolved_duration_mode == "fixed" and resolved_duration is not None:
            shot.duration_sec = resolved_duration
        shot.shot_card = shot_card_with_duration_mode(
            shot.shot_card,
            resolved_duration_mode,
            planned_duration_sec=(
                resolved_duration if resolved_duration_mode == "fixed" else None
            ),
            duration_source=(
                "manual" if duration_mode == "fixed" or duration_sec is not None else None
            ),
        )
        if video_prompt is not None:
            shot.video_prompt = video_prompt.strip() or None
            shot.shot_card = shot_card_with_video_prompt(
                shot.shot_card,
                shot.video_prompt,
            )
        db.add(shot)
        db.flush()

    image_asset = resolve_shot_video_input_asset(db, shot)
    asset_resolution = resolve_generation_assets(
        db,
        shot.project_id,
        stage="shot_video",
        shot=shot,
        fallback_image_asset=image_asset,
    )
    shot_characters, scene, shot_props = shot_entities_from_context(
        shot,
        project_prompt_context(db, shot.project_id),
    )
    prompt = shot_video_prompt(
        shot,
        characters=shot_characters,
        scene=scene,
        props=shot_props,
    )
    video_params = shot_video_provider_params(
        shot,
        provider,
        duration_mode=resolved_duration_mode,
        duration_sec=resolved_duration,
    )
    reference_metadata = asset_resolution.reference_metadata
    request_metadata = with_avd_asset_usage(
        {
            "image_asset_id": image_asset.id if image_asset else None,
            "asset_resolution": asset_resolution.to_task_payload(),
            "candidate_policy": "pending_review_before_asset",
            "input_fingerprint": (shot.shot_card or {}).get("prompt_fingerprint"),
            "provider_capabilities": public_provider_capabilities(provider),
            "duration_request": duration_request_metadata(video_params),
        },
        asset_type="video",
        asset_role="shot_video",
        entity_type="shot",
    )
    payload = {
        "shot_id": shot.id,
        "shot_no": shot.shot_no,
        "duration_mode": resolved_duration_mode,
        "duration_sec": (
            str(resolved_duration) if resolved_duration is not None else None
        ),
        "video_prompt": shot.video_prompt,
        "image_asset_id": image_asset.id if image_asset else None,
        "video_reference_source": "manual" if image_asset else "none",
        "candidate_policy": "pending_review_before_asset",
        "asset_resolution": asset_resolution.to_task_payload(),
        "source_asset_id": source_asset.id if source_asset else None,
        "source_asset_version": source_asset.version if source_asset else None,
        "provider_request": {
            "model": resolved_model,
            "prompt": prompt,
            "negative_prompt": shot.negative_prompt,
            "references": list(asset_resolution.references),
            "params": {
                **video_params,
                "reference_mode": reference_metadata.get("reference_mode", "none"),
            },
            "metadata": {
                "entity_type": "shot",
                "entity_id": shot.id,
                "image_asset_id": image_asset.id if image_asset else None,
                "asset_resolution": asset_resolution.to_task_payload(),
                "candidate_policy": "pending_review_before_asset",
                "avd_asset_purpose": request_metadata["avd_asset_purpose"],
                "avd_asset_usage": request_metadata["avd_asset_usage"],
                "duration_request": request_metadata["duration_request"],
                **reference_metadata,
            },
        },
        "request_metadata": request_metadata,
        "shot_prompt_audits": [
            {
                "shot_id": shot.id,
                "final_prompt": prompt,
                "input_fingerprint": (shot.shot_card or {}).get("prompt_fingerprint"),
                "reference_asset_ids": reference_asset_ids(asset_resolution),
                "provider": provider.name,
                "model": resolved_model,
                "duration_request": duration_request_metadata(video_params),
                "provider_capabilities": public_provider_capabilities(provider),
            }
        ],
    }
    return payload, provider


def shot_video_provider_params(
    shot: Shot,
    provider: Any,
    *,
    duration_mode: str | None = None,
    duration_sec: Decimal | None = None,
) -> dict[str, Any]:
    resolved_mode, resolved_duration = resolve_video_duration_request(
        provider,
        shot,
        duration_mode=duration_mode,
        duration_sec=duration_sec,
    )
    provider_name = str(getattr(provider, "name", ""))
    if provider_name == "seedance2_api":
        return {
            "duration_mode": resolved_mode,
            "duration": -1 if resolved_mode == "provider_auto" else float(resolved_duration),
            "ratio": str(getattr(shot.project, "aspect_ratio", None) or settings.seedance2_ratio),
            "resolution": settings.seedance2_resolution,
            "generate_audio": settings.seedance2_generate_audio,
            "watermark": settings.seedance2_watermark,
        }
    if provider_name == "minimax_h3_gateway":
        ratio = str(getattr(shot.project, "aspect_ratio", None) or "16:9")
        size = (
            settings.minimax_h3_gateway_portrait_size
            if ratio == "9:16"
            else settings.minimax_h3_gateway_landscape_size
        )
        params: dict[str, Any] = {
            "duration_mode": resolved_mode,
            "duration_sec": float(resolved_duration or Decimal("5")),
            "ratio": ratio,
            "size": size,
            "steps": settings.minimax_h3_gateway_steps,
        }
        if settings.minimax_h3_gateway_seed is not None:
            params["seed"] = settings.minimax_h3_gateway_seed
        assert_video_provider_duration(
            provider,
            params["duration_sec"],
            shot_no=shot.shot_no,
        )
        return params
    if provider_name == "mock":
        params = {
            **shot_video_params(
                shot,
                fps=settings.wan_i2v_fps,
                max_frames=settings.wan_i2v_frames,
                default_steps=settings.wan_i2v_steps,
                default_guidance=settings.wan_i2v_guidance,
            ),
            "ratio": str(getattr(shot.project, "aspect_ratio", None) or "16:9"),
            "resolution": str(getattr(shot.project, "resolution", None) or "854x480"),
        }
    elif provider_name == "ltx23_api":
        params = shot_video_params(
            shot,
            fps=max(1, round(settings.ltx23_fps)),
            max_frames=settings.ltx23_frames,
            default_steps=settings.ltx23_steps,
            default_guidance=settings.ltx23_cfg,
        )
    else:
        params = shot_video_params(
            shot,
            fps=settings.wan_i2v_fps,
            max_frames=settings.wan_i2v_frames,
            default_steps=settings.wan_i2v_steps,
            default_guidance=settings.wan_i2v_guidance,
        )
    if resolved_duration is not None:
        params["duration_sec"] = float(resolved_duration)
    params["duration_mode"] = resolved_mode
    assert_video_provider_duration(
        provider,
        params.get("duration_sec"),
        shot_no=shot.shot_no,
    )
    return params


def resolve_video_duration_request(
    provider: Any,
    shot: Shot,
    *,
    duration_mode: str | None,
    duration_sec: Decimal | None,
) -> tuple[str, Decimal | None]:
    mode = str(duration_mode or "").strip()
    planned_mode, planned_duration = _shot_duration_plan(shot)
    if mode and mode not in {"provider_auto", "fixed"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Unsupported video duration mode",
        )
    if not mode:
        if duration_sec is not None:
            mode = "fixed"
        elif planned_mode == "provider_auto" and bool(getattr(provider, "smart_duration", False)):
            mode = "provider_auto"
        elif planned_duration is not None:
            mode = "fixed"
        elif bool(getattr(provider, "smart_duration", False)):
            mode = "provider_auto"
        else:
            mode = "fixed"

    if mode == "provider_auto":
        if duration_sec is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Smart duration cannot include duration_sec",
            )
        if not bool(getattr(provider, "smart_duration", False)):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "video_smart_duration_unsupported",
                    "message": "当前视频模型不支持智能时长，请使用固定时长。",
                    "provider": str(getattr(provider, "name", "video_provider")),
                    "model": str(getattr(provider, "model", "")),
                },
            )
        return mode, None

    resolved_duration = (
        duration_sec
        if duration_sec is not None
        else planned_duration
        if planned_duration is not None
        else shot.duration_sec
    )
    if duration_mode == "fixed" and resolved_duration is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Fixed duration requires duration_sec",
        )
    if resolved_duration is not None:
        assert_video_provider_duration(
            provider,
            resolved_duration,
            shot_no=shot.shot_no,
        )
    return mode, resolved_duration


def duration_request_metadata(video_params: dict[str, Any]) -> dict[str, Any]:
    mode = str(video_params.get("duration_mode") or "fixed")
    requested = video_params.get("duration")
    if requested is None:
        requested = video_params.get("duration_sec")
    return {
        "mode": mode,
        "provider_value": requested,
        "requested_duration_sec": None if mode == "provider_auto" else requested,
    }


def public_provider_capabilities(provider: Any) -> dict[str, Any]:
    descriptor = provider.descriptor()
    return {
        key: descriptor.get(key)
        for key in (
            "native_audio",
            "reference_images",
            "reference_videos",
            "reference_audio",
            "multi_reference",
            "smart_duration",
            "min_duration_sec",
            "max_duration_sec",
            "supported_resolutions",
        )
    }


def assert_video_provider_duration(
    provider: Any,
    duration_sec: object,
    *,
    shot_no: int | None = None,
) -> None:
    min_duration = getattr(provider, "min_duration_sec", None)
    max_duration = getattr(provider, "max_duration_sec", None)
    if min_duration is None and max_duration is None:
        return
    try:
        duration = float(duration_sec)
    except (TypeError, ValueError):
        return
    supported_min = float(min_duration) if min_duration is not None else None
    supported_max = float(max_duration) if max_duration is not None else None
    if (
        (supported_min is None or duration >= supported_min)
        and (supported_max is None or duration <= supported_max)
    ):
        return
    provider_name = str(getattr(provider, "name", "video_provider"))
    model = str(getattr(provider, "model", ""))
    shot_label = f"片段 {shot_no}" if shot_no is not None else "当前片段"
    range_text = (
        f"{supported_min:g}-{supported_max:g} 秒"
        if supported_min is not None and supported_max is not None
        else f"至少 {supported_min:g} 秒"
        if supported_min is not None
        else f"最多 {supported_max:g} 秒"
    )
    error_code = (
        "video_duration_below_provider_minimum"
        if supported_min is not None and duration < supported_min
        else "video_duration_exceeds_provider_limit"
    )
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": error_code,
            "message": (
                f"{shot_label} 的固定时长为 {duration:g} 秒，不符合当前视频模型 "
                f"{model or provider_name} 支持的 {range_text}；"
                "请使用智能时长或调整固定值"
            ),
            "provider": provider_name,
            "model": model,
            "shot_duration_sec": duration,
            "min_duration_sec": supported_min,
            "max_duration_sec": supported_max,
        },
    )


def reference_asset_ids(asset_resolution: Any) -> list[str]:
    metadata = (
        asset_resolution.reference_metadata
        if isinstance(getattr(asset_resolution, "reference_metadata", None), dict)
        else {}
    )
    values = metadata.get("reference_asset_ids")
    if not isinstance(values, list):
        return []
    return [str(item) for item in values if str(item).strip()]


def shot_card_with_video_prompt(card: object, video_prompt: str | None) -> dict[str, Any]:
    updated = dict(card) if isinstance(card, dict) else {}
    if not video_prompt:
        return updated
    updated["video_prompt_override"] = {
        "value": video_prompt,
        "scope": "single_shot_generation",
        "note": "Do not copy this full prompt into action or frame_plan fields.",
    }
    return updated


def shot_card_with_duration_mode(
    card: object,
    duration_mode: str,
    *,
    planned_duration_sec: Decimal | None = None,
    duration_source: str | None = None,
) -> dict[str, Any]:
    updated = dict(card) if isinstance(card, dict) else {}
    segment_plan = (
        dict(updated.get("segment_plan"))
        if isinstance(updated.get("segment_plan"), dict)
        else {}
    )
    segment_plan["duration_mode"] = duration_mode
    if duration_mode == "fixed" and planned_duration_sec is not None:
        segment_plan["planned_duration_sec"] = str(planned_duration_sec)
        if duration_source:
            segment_plan["duration_source"] = duration_source
        else:
            segment_plan.setdefault("duration_source", "runtime")
    updated["segment_plan"] = segment_plan
    return updated


def _shot_duration_plan(shot: Shot) -> tuple[str, Decimal | None]:
    raw_card = getattr(shot, "shot_card", None)
    card = raw_card if isinstance(raw_card, dict) else {}
    segment_plan = (
        card.get("segment_plan")
        if isinstance(card.get("segment_plan"), dict)
        else {}
    )
    return (
        str(segment_plan.get("duration_mode") or ""),
        decimal_or_none(segment_plan.get("planned_duration_sec")),
    )


def decimal_or_none(value: object) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return Decimal(str(value))
    except (ArithmeticError, TypeError, ValueError):
        return None


def project_prompt_context(db: Session, project_id: str) -> dict[str, Any]:
    characters, scenes, props = get_current_entities(db, project_id)
    return {
        "characters": {item.id: item for item in characters},
        "scenes": {item.id: item for item in scenes},
        "props": {item.id: item for item in props},
    }


def shot_entities_from_context(
    shot: Shot,
    prompt_context: dict[str, Any] | None,
) -> tuple[list[Character], Scene | None, list[Prop]]:
    prompt_context = prompt_context or {}
    character_map = prompt_context.get("characters") or {}
    scene_map = prompt_context.get("scenes") or {}
    prop_map = prompt_context.get("props") or {}
    characters = [
        character_map[item]
        for item in (shot.character_ids or [])
        if isinstance(item, str) and item in character_map
    ]
    scene = scene_map.get(shot.scene_id) if shot.scene_id else None
    props = [
        prop_map[item]
        for item in (shot.prop_ids or [])
        if isinstance(item, str) and item in prop_map
    ]
    return characters, scene, props
