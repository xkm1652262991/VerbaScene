from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import ProviderConfig
from app.providers.base import ProviderAdapter
from app.providers.comfyui_flux import ComfyUIFluxProvider
from app.providers.custom_image_http import CustomImageHTTPProvider
from app.providers.dashscope_image import DashScopeImageProvider
from app.providers.defaults import provider_registry
from app.providers.gemini import GeminiImageProvider
from app.providers.mock import MockImageProvider
from app.providers.openai_image import OpenAICompatibleImageProvider
from app.providers.qwen_image_musubi import QwenImageMusubiProvider
from app.providers.types import ProviderType
from app.services.provider_config_service import baseline_provider_snapshot, resolve_provider_config_api_key


ENVIRONMENT_IMAGE_PROFILE_ID = "environment:image"


@dataclass(frozen=True)
class ImageProviderSelection:
    provider: ProviderAdapter
    profile_id: str | None
    default_params: dict[str, Any]
    source: str


def list_image_provider_profiles(db: Session) -> list[dict[str, Any]]:
    profiles: list[dict[str, Any]] = []
    seen_provider_names: set[str] = set()
    rows = list(
        db.scalars(
            select(ProviderConfig)
            .where(ProviderConfig.workspace_id.is_(None))
            .where(ProviderConfig.provider_type == ProviderType.IMAGE.value)
            .order_by(ProviderConfig.updated_at.desc())
        ).all()
    )
    for config in rows:
        if config.provider_name in seen_provider_names:
            continue
        seen_provider_names.add(config.provider_name)
        profiles.append(_serialize_saved_profile(config, is_default=bool(config.enabled)))

    environment = baseline_provider_snapshot(ProviderType.IMAGE.value)
    environment_identity = (
        environment["provider_name"],
        environment["model_name"],
        environment["base_url"],
    )
    saved_identities = {
        (item["provider_name"], item["model_name"], item["base_url"])
        for item in profiles
    }
    if environment_identity not in saved_identities:
        profiles.append(_serialize_environment_profile(environment, is_default=not profiles))
    return sorted(profiles, key=lambda item: (not item["is_default"], item["label"].lower()))


def resolve_image_provider_selection(
    db: Session,
    profile_id: str | None,
) -> ImageProviderSelection:
    if not profile_id:
        provider = provider_registry.get(ProviderType.IMAGE, settings.image_provider)
        return ImageProviderSelection(provider=provider, profile_id=None, default_params={}, source="runtime_default")

    if profile_id == ENVIRONMENT_IMAGE_PROFILE_ID:
        snapshot = baseline_provider_snapshot(ProviderType.IMAGE.value)
        provider = _build_image_provider(snapshot)
        _ensure_ready(provider)
        return ImageProviderSelection(
            provider=provider,
            profile_id=profile_id,
            default_params=dict(snapshot["default_params"]),
            source="environment",
        )

    config = db.get(ProviderConfig, profile_id)
    if config is None or config.provider_type != ProviderType.IMAGE.value or config.workspace_id is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image provider profile not found")
    snapshot = {
        "provider_name": config.provider_name,
        "model_name": config.model_name,
        "base_url": config.base_url,
        "api_key": resolve_provider_config_api_key(config),
        "default_params": dict(config.default_params or {}),
    }
    provider = _build_image_provider(snapshot)
    _ensure_ready(provider)
    return ImageProviderSelection(
        provider=provider,
        profile_id=config.id,
        default_params=dict(config.default_params or {}),
        source="saved",
    )


def _serialize_saved_profile(config: ProviderConfig, *, is_default: bool) -> dict[str, Any]:
    snapshot = {
        "provider_name": config.provider_name,
        "model_name": config.model_name,
        "base_url": config.base_url,
        "api_key": resolve_provider_config_api_key(config),
        "default_params": dict(config.default_params or {}),
    }
    return _serialize_profile(config.id, snapshot, source="saved", is_default=is_default)


def _serialize_environment_profile(snapshot: dict[str, Any], *, is_default: bool) -> dict[str, Any]:
    return _serialize_profile(
        ENVIRONMENT_IMAGE_PROFILE_ID,
        snapshot,
        source="environment",
        is_default=is_default,
    )


def _serialize_profile(
    profile_id: str,
    snapshot: dict[str, Any],
    *,
    source: str,
    is_default: bool,
) -> dict[str, Any]:
    try:
        provider = _build_image_provider(snapshot)
        provider.validate_config()
    except (KeyError, ValueError) as exc:
        capabilities: list[str] = []
        validation_error = str(exc)
    else:
        capabilities = list(provider.capabilities)
        validation_error = None
    provider_name = str(snapshot["provider_name"])
    model_name = str(snapshot["model_name"])
    return {
        "id": profile_id,
        "label": f"{model_name} · {provider_name}",
        "provider_name": provider_name,
        "model_name": model_name,
        "base_url": snapshot.get("base_url"),
        "capabilities": capabilities,
        "default_params": dict(snapshot.get("default_params") or {}),
        "source": source,
        "is_default": is_default,
        "supports_references": "reference_payload" in capabilities or "reference_to_image" in capabilities,
        "configuration_status": "incomplete" if validation_error else "ready",
        "validation_error": validation_error,
    }


def _build_image_provider(snapshot: dict[str, Any]) -> ProviderAdapter:
    provider_name = str(snapshot["provider_name"])
    model_name = str(snapshot["model_name"])
    base_url = snapshot.get("base_url")
    api_key = snapshot.get("api_key")
    default_params = dict(snapshot.get("default_params") or {})
    if provider_name == "openai_image":
        return OpenAICompatibleImageProvider(
            model=model_name,
            base_url=base_url,
            api_key=api_key,
            default_params=default_params,
        )
    if provider_name == "custom_image_http":
        return CustomImageHTTPProvider(
            model=model_name,
            base_url=base_url,
            api_key=api_key,
            default_params=default_params,
        )
    if provider_name == "dashscope_image":
        return DashScopeImageProvider(
            model=model_name,
            base_url=base_url,
            api_key=api_key,
            default_params=default_params,
        )
    if provider_name == "qwen_image_musubi":
        return QwenImageMusubiProvider(
            model=model_name,
            base_url=base_url,
            default_params=default_params,
        )
    if provider_name == "gemini":
        return GeminiImageProvider(model=model_name, base_url=base_url, api_key=api_key)
    if provider_name == "comfyui_flux":
        return ComfyUIFluxProvider(model=model_name, base_url=base_url)
    if provider_name == "mock":
        provider = MockImageProvider()
        provider.model = model_name
        return provider
    raise ValueError(f"Unsupported image provider profile: {provider_name}")


def _ensure_ready(provider: ProviderAdapter) -> None:
    try:
        provider.validate_config()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Image provider profile is incomplete: {exc}",
        ) from exc
