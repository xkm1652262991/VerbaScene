from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from app.agents import shot_video_prompt
from app.core.config import settings
from app.models import Asset, Shot
from app.providers.base import ProviderAdapter
from app.providers.defaults import provider_registry
from app.providers.types import ProviderType
from app.services.asset_repository import resolve_shot_video_input_asset
from app.services.asset_resolver_service import resolve_generation_assets
from app.services.media_generation_support import with_avd_asset_usage
from app.services.video_generation_service import (
    _duration_request_metadata,
    _project_prompt_context,
    _public_provider_capabilities,
    _reference_asset_ids,
    _resolve_video_duration_request,
    _shot_card_with_duration_mode,
    _shot_card_with_video_prompt,
    _shot_entities_from_context,
    _shot_video_provider_params,
)


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
    resolved_duration_mode, resolved_duration = _resolve_video_duration_request(
        provider,
        shot,
        duration_mode=duration_mode,
        duration_sec=duration_sec,
    )
    if update_shot:
        if resolved_duration_mode == "fixed" and resolved_duration is not None:
            shot.duration_sec = resolved_duration
        shot.shot_card = _shot_card_with_duration_mode(
            shot.shot_card,
            resolved_duration_mode,
        )
        if video_prompt is not None:
            shot.video_prompt = video_prompt.strip() or None
            shot.shot_card = _shot_card_with_video_prompt(
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
    shot_characters, scene, shot_props = _shot_entities_from_context(
        shot,
        _project_prompt_context(db, shot.project_id),
    )
    prompt = shot_video_prompt(
        shot,
        characters=shot_characters,
        scene=scene,
        props=shot_props,
    )
    video_params = _shot_video_provider_params(
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
            "provider_capabilities": _public_provider_capabilities(provider),
            "duration_request": _duration_request_metadata(video_params),
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
                "reference_asset_ids": _reference_asset_ids(asset_resolution),
                "provider": provider.name,
                "model": resolved_model,
                "duration_request": _duration_request_metadata(video_params),
                "provider_capabilities": _public_provider_capabilities(provider),
            }
        ],
    }
    return payload, provider
