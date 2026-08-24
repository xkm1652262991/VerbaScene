from __future__ import annotations

import os
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import ProviderConfig
from app.providers.defaults import provider_registry, refresh_provider_registry
from app.providers.types import ProviderType
from app.schemas.provider import ProviderConfigUpsert
from app.services.provider_secret_store import (
    delete_provider_secret,
    get_provider_secret,
    has_provider_secret,
    set_provider_secret,
)


PROVIDER_SLOTS = ("llm", "image", "video")
DIRECT_API_KEY_REF = "__LOCAL_DIRECT__"
NO_API_KEY_REF = "__NO_API_KEY__"

PROVIDER_CATALOG: dict[str, tuple[str, ...]] = {
    "llm": ("mock", "dashscope", "openai_compatible"),
    "image": (
        "mock",
        "gemini",
        "dashscope_image",
        "openai_image",
        "custom_image_http",
        "qwen_image_musubi",
        "comfyui_flux",
    ),
    "video": ("mock", "seedance2_api", "ltx23_api", "wan2_i2v_api"),
}

SELECTION_SETTINGS = {
    "llm": "llm_provider",
    "image": "image_provider",
    "video": "video_provider",
}

MODEL_SETTINGS: dict[tuple[str, str], tuple[str, ...]] = {
    ("llm", "mock"): ("llm_model",),
    ("llm", "dashscope"): ("llm_model", "dashscope_model"),
    ("llm", "openai_compatible"): ("llm_model",),
    ("image", "mock"): ("image_model",),
    ("image", "gemini"): ("image_model", "gemini_image_model"),
    ("image", "dashscope_image"): ("image_model", "dashscope_image_model"),
    ("image", "openai_image"): ("image_model",),
    ("image", "custom_image_http"): ("image_model",),
    ("image", "qwen_image_musubi"): ("image_model", "qwen_image_musubi_model"),
    ("image", "comfyui_flux"): ("image_model", "comfyui_flux_model"),
    ("video", "mock"): ("video_model",),
    ("video", "seedance2_api"): ("video_model", "seedance2_model"),
    ("video", "ltx23_api"): ("video_model",),
    ("video", "wan2_i2v_api"): ("video_model",),
}

BASE_URL_SETTINGS: dict[tuple[str, str], str | None] = {
    ("llm", "mock"): None,
    ("llm", "dashscope"): "dashscope_base_url",
    ("llm", "openai_compatible"): "llm_base_url",
    ("image", "mock"): None,
    ("image", "gemini"): "gemini_base_url",
    ("image", "dashscope_image"): "dashscope_image_base_url",
    ("image", "openai_image"): "image_base_url",
    ("image", "custom_image_http"): "image_base_url",
    ("image", "qwen_image_musubi"): "qwen_image_musubi_base_url",
    ("image", "comfyui_flux"): "comfyui_flux_base_urls",
    ("video", "mock"): None,
    ("video", "seedance2_api"): "seedance2_api_base_url",
    ("video", "ltx23_api"): "ltx23_api_base_url",
    ("video", "wan2_i2v_api"): "wan_i2v_api_base_url",
}

API_KEY_SETTINGS: dict[tuple[str, str], str | None] = {
    ("llm", "mock"): None,
    ("llm", "dashscope"): "dashscope_api_key",
    ("llm", "openai_compatible"): "llm_api_key",
    ("image", "mock"): None,
    ("image", "gemini"): "gemini_api_key",
    ("image", "dashscope_image"): "dashscope_image_api_key",
    ("image", "openai_image"): "image_api_key",
    ("image", "custom_image_http"): "image_api_key",
    ("image", "qwen_image_musubi"): None,
    ("image", "comfyui_flux"): None,
    ("video", "mock"): None,
    ("video", "seedance2_api"): "seedance2_api_key",
    ("video", "ltx23_api"): None,
    ("video", "wan2_i2v_api"): "wan_i2v_api_key",
}

API_KEY_REF_CANDIDATES: dict[tuple[str, str], tuple[str, ...]] = {
    ("llm", "mock"): (),
    ("llm", "dashscope"): ("DASHSCOPE_API_KEY", "LLM_API_KEY"),
    ("llm", "openai_compatible"): ("LLM_API_KEY",),
    ("image", "mock"): (),
    ("image", "gemini"): ("GEMINI_API_KEY", "IMAGE_API_KEY"),
    ("image", "dashscope_image"): ("DASHSCOPE_IMAGE_API_KEY", "DASHSCOPE_API_KEY"),
    ("image", "openai_image"): ("IMAGE_API_KEY",),
    ("image", "custom_image_http"): ("IMAGE_API_KEY",),
    ("image", "qwen_image_musubi"): (),
    ("image", "comfyui_flux"): (),
    ("video", "mock"): (),
    ("video", "seedance2_api"): (
        "SEEDANCE2_API_KEY",
        "ARK_API_KEY",
        "VIDEO_API_KEY",
    ),
    ("video", "ltx23_api"): (),
    ("video", "wan2_i2v_api"): ("WAN_I2V_API_KEY", "VIDEO_API_KEY"),
}


@dataclass(frozen=True)
class ParamSpec:
    setting_name: str
    coerce: Callable[[Any], Any]


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    raise ValueError(f"Not a boolean: {value!r}")


def _coerce_image_reference_transport(value: Any) -> str:
    normalized = str(value).strip().lower()
    if normalized not in {"none", "prompt", "payload", "hybrid"}:
        raise ValueError(f"Unsupported image reference transport: {value!r}")
    return normalized


def _coerce_image_quality(value: Any) -> str:
    normalized = str(value).strip().lower()
    if normalized not in {"low", "medium", "high", "auto"}:
        raise ValueError(f"Unsupported image quality: {value!r}")
    return normalized


PARAM_SPECS: dict[str, dict[str, ParamSpec]] = {
    "llm": {},
    "image": {
        "size": ParamSpec("image_size", str),
        # Legacy compatibility for already-saved configs. The image workflow
        # intentionally ignores this value and always creates one candidate
        # per target per click.
        "candidate_count": ParamSpec("image_candidate_count", int),
        "quality": ParamSpec("image_quality", _coerce_image_quality),
        "steps": ParamSpec("image_num_inference_steps", int),
        "guidance_scale": ParamSpec("image_guidance_scale", float),
        "reference_transport": ParamSpec("image_reference_transport", _coerce_image_reference_transport),
        "endpoint": ParamSpec("custom_image_endpoint", str),
        "supports_references": ParamSpec("image_supports_references", _coerce_bool),
        "prompt_extend": ParamSpec("dashscope_image_prompt_extend", _coerce_bool),
        "watermark": ParamSpec("dashscope_image_watermark", _coerce_bool),
    },
    "video": {
        "protocol": ParamSpec("wan_i2v_api_protocol", str),
        "mode": ParamSpec("wan_i2v_api_mode", str),
        "gpu_devices": ParamSpec("wan_i2v_api_gpu_devices", str),
        "size": ParamSpec("wan_i2v_size", str),
        "seed": ParamSpec("wan_i2v_seed", int),
        "frames": ParamSpec("wan_i2v_frames", int),
        "steps": ParamSpec("wan_i2v_steps", int),
        "guidance": ParamSpec("wan_i2v_guidance", float),
        "fps": ParamSpec("wan_i2v_fps", int),
        "max_area": ParamSpec("wan_i2v_max_area", int),
        "upscale_1080p": ParamSpec("wan_i2v_upscale_1080p", _coerce_bool),
        "result_variant": ParamSpec("wan_i2v_api_result_variant", str),
        "image_transport": ParamSpec("wan_i2v_image_transport", str),
    },
}

PROVIDER_PARAM_SPECS: dict[tuple[str, str], dict[str, ParamSpec]] = {
    ("video", "seedance2_api"): {
        "resolution": ParamSpec("seedance2_resolution", str),
        "generate_audio": ParamSpec("seedance2_generate_audio", _coerce_bool),
        "watermark": ParamSpec("seedance2_watermark", _coerce_bool),
    },
    ("video", "ltx23_api"): {
        "size": ParamSpec("ltx23_size", str),
        "seed": ParamSpec("ltx23_seed", int),
        "frames": ParamSpec("ltx23_frames", int),
        "steps": ParamSpec("ltx23_steps", int),
        "guidance": ParamSpec("ltx23_cfg", float),
        "fps": ParamSpec("ltx23_fps", float),
        "strength": ParamSpec("ltx23_strength", float),
    },
}

_MUTABLE_SETTING_NAMES = {
    *SELECTION_SETTINGS.values(),
    *(name for names in MODEL_SETTINGS.values() for name in names),
    *(name for name in BASE_URL_SETTINGS.values() if name),
    *(name for name in API_KEY_SETTINGS.values() if name),
    *(spec.setting_name for specs in PARAM_SPECS.values() for spec in specs.values()),
    *(
        spec.setting_name
        for specs in PROVIDER_PARAM_SPECS.values()
        for spec in specs.values()
    ),
}
_BASELINE_SETTINGS = {
    name: deepcopy(getattr(settings, name))
    for name in _MUTABLE_SETTING_NAMES
}
_ENV_TO_SETTING = {
    setting_name.upper(): setting_name
    for setting_name in _MUTABLE_SETTING_NAMES
    if setting_name.endswith("_api_key")
}


def list_runtime_provider_configs(db: Session) -> list[dict[str, Any]]:
    active = _active_configs(db)
    return [_serialize_runtime_config(slot, active.get(slot)) for slot in PROVIDER_SLOTS]


def upsert_runtime_provider_config(
    db: Session,
    provider_type: str,
    payload: ProviderConfigUpsert,
) -> dict[str, Any]:
    _validate_slot_and_provider(provider_type, payload.provider_name)
    normalized_params = _normalize_default_params(
        provider_type,
        payload.provider_name,
        payload.default_params,
    )
    _validate_provider_default_params(provider_type, payload.provider_name, normalized_params)

    existing_configs = list(db.scalars(
        select(ProviderConfig)
        .where(ProviderConfig.workspace_id.is_(None))
        .where(ProviderConfig.provider_type == provider_type)
        .where(ProviderConfig.enabled.is_(True))
    ).all())
    previous_secret = get_provider_secret(provider_type, payload.provider_name)
    stored_api_key_ref = _configure_api_key(provider_type, payload)

    for existing in existing_configs:
        existing.enabled = False
        db.add(existing)

    config = ProviderConfig(
        workspace_id=None,
        provider_type=provider_type,
        provider_name=payload.provider_name,
        model_name=payload.model_name,
        api_key_ref=stored_api_key_ref,
        base_url=payload.base_url,
        default_params=normalized_params,
        enabled=True,
    )
    db.add(config)
    try:
        db.commit()
    except Exception:
        db.rollback()
        if previous_secret is None:
            delete_provider_secret(provider_type, payload.provider_name)
        else:
            set_provider_secret(provider_type, payload.provider_name, previous_secret)
        raise
    db.refresh(config)
    for existing in existing_configs:
        if existing.api_key_ref == DIRECT_API_KEY_REF and existing.provider_name != payload.provider_name:
            delete_provider_secret(provider_type, existing.provider_name)
    reload_runtime_provider_configs(db)
    return _serialize_runtime_config(provider_type, config)


def reset_runtime_provider_config(db: Session, provider_type: str) -> dict[str, Any]:
    _validate_slot(provider_type)
    active_configs = list(db.scalars(
        select(ProviderConfig)
        .where(ProviderConfig.workspace_id.is_(None))
        .where(ProviderConfig.provider_type == provider_type)
        .where(ProviderConfig.enabled.is_(True))
    ).all())
    for config in active_configs:
        config.enabled = False
        db.add(config)
    db.commit()
    for config in active_configs:
        if config.api_key_ref == DIRECT_API_KEY_REF:
            delete_provider_secret(provider_type, config.provider_name)
    reload_runtime_provider_configs(db)
    return _serialize_runtime_config(provider_type, None)


def reload_runtime_provider_configs(db: Session) -> None:
    """Apply the latest saved slot configurations to this process.

    Adapter instances capture endpoint and credential fields during
    construction, so a registry rebuild is required. The registry object itself
    is preserved to keep imports in generation services live.
    """
    for name, value in _BASELINE_SETTINGS.items():
        setattr(settings, name, deepcopy(value))

    active = _active_configs(db)
    for slot, config in active.items():
        _apply_config_values(slot, config)

    # Adapters inspect the active provider name while they capture endpoint
    # settings in their constructors. Apply selections before rebuilding them.
    for slot, config in active.items():
        setattr(settings, SELECTION_SETTINGS[slot], config.provider_name)

    refresh_provider_registry()

    # Mock providers keep their model as a class default. Align every selected
    # adapter descriptor with the saved model so task records and the UI agree.
    for slot, config in active.items():
        provider = provider_registry.get(ProviderType(slot), config.provider_name)
        provider.model = config.model_name

def _active_configs(db: Session) -> dict[str, ProviderConfig]:
    rows = db.scalars(
        select(ProviderConfig)
        .where(ProviderConfig.workspace_id.is_(None))
        .where(ProviderConfig.enabled.is_(True))
        .order_by(ProviderConfig.provider_type, ProviderConfig.updated_at.desc())
    ).all()
    active: dict[str, ProviderConfig] = {}
    for row in rows:
        if row.provider_type in PROVIDER_SLOTS:
            active.setdefault(row.provider_type, row)
    return active


def _apply_config_values(slot: str, config: ProviderConfig) -> None:
    key = (slot, config.provider_name)
    for setting_name in MODEL_SETTINGS[key]:
        setattr(settings, setting_name, config.model_name)

    base_setting = BASE_URL_SETTINGS[key]
    if base_setting and config.base_url is not None:
        setattr(settings, base_setting, config.base_url)

    key_setting = API_KEY_SETTINGS[key]
    if key_setting:
        if config.api_key_ref == DIRECT_API_KEY_REF:
            setattr(settings, key_setting, get_provider_secret(slot, config.provider_name))
        elif config.api_key_ref == NO_API_KEY_REF:
            setattr(settings, key_setting, None)
        elif config.api_key_ref:
            setattr(settings, key_setting, _resolve_api_key(config.api_key_ref))

    normalized_params = _normalize_default_params(
        slot,
        config.provider_name,
        config.default_params or {},
    )
    _validate_provider_default_params(slot, config.provider_name, normalized_params)
    param_specs = _param_specs(slot, config.provider_name)
    for name, value in normalized_params.items():
        setattr(settings, param_specs[name].setting_name, value)


def _validate_provider_default_params(slot: str, provider_name: str, params: dict[str, Any]) -> None:
    if slot != "video":
        return
    if provider_name == "seedance2_api":
        resolution = str(
            params.get("resolution", settings.seedance2_resolution)
        ).strip().lower()
        if resolution not in {"480p", "720p", "1080p"}:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Seedance 2.0 分辨率必须是 480p、720p 或 1080p。",
            )
        return
    if provider_name == "ltx23_api":
        size = str(params.get("size", settings.ltx23_size)).lower().replace("*", "x")
        try:
            width_text, height_text = size.split("x", 1)
            width, height = int(width_text), int(height_text)
            frames = int(params.get("frames", settings.ltx23_frames))
            steps = int(params.get("steps", settings.ltx23_steps))
            fps = float(params.get("fps", settings.ltx23_fps))
            cfg = float(params.get("guidance", settings.ltx23_cfg))
            strength = float(params.get("strength", settings.ltx23_strength))
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="LTX-2.3 视频参数格式无效。",
            ) from exc
        if width < 1 or height < 1 or frames < 1 or steps < 1 or fps <= 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="LTX-2.3 尺寸、帧数、采样步数和 FPS 必须为正数。",
            )
        if cfg < 0 or not 0 <= strength <= 1:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="LTX-2.3 CFG 不能小于 0，strength 必须在 0 到 1 之间。",
            )
        return
    if provider_name != "wan2_i2v_api":
        return
    protocol = str(params.get("protocol", settings.wan_i2v_api_protocol)).strip().lower()
    if protocol != "official":
        return
    frames = int(params.get("frames", settings.wan_i2v_frames))
    steps = int(params.get("steps", settings.wan_i2v_steps))
    guidance = float(params.get("guidance", settings.wan_i2v_guidance))
    if frames < 1 or (frames - 1) % 4 != 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Wan official 协议要求 frames=4n+1（例如 17、49、81）。",
        )
    if steps != 4 or guidance != 1.0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Wan official 18083 服务固定要求 steps=4、guidance=1。",
        )


def _serialize_runtime_config(slot: str, config: ProviderConfig | None) -> dict[str, Any]:
    if config is None:
        provider_name = str(getattr(settings, SELECTION_SETTINGS[slot]))
        model_name = _current_model(slot, provider_name)
        base_url = _current_base_url(slot, provider_name)
        api_key_ref = _default_api_key_ref(slot, provider_name)
        api_key_mode = "environment" if api_key_ref else "none"
        api_key_configured = bool(_resolve_api_key(api_key_ref)) if api_key_ref else False
        params = _current_default_params(slot, provider_name)
        source = "environment"
    else:
        provider_name = config.provider_name
        model_name = config.model_name
        base_url = config.base_url if config.base_url is not None else _current_base_url(slot, provider_name)
        api_key_mode, api_key_ref, api_key_configured = _serialized_api_key_state(
            slot,
            provider_name,
            config.api_key_ref,
        )
        params = config.default_params or {}
        source = "saved"

    validation_error = _provider_validation_error(slot, provider_name)
    descriptor = provider_registry.get(ProviderType(slot), provider_name).descriptor()
    return {
        "id": config.id if config else None,
        "provider_type": slot,
        "provider_name": provider_name,
        "model_name": model_name,
        "base_url": base_url,
        "api_key_ref": api_key_ref,
        "api_key_mode": api_key_mode,
        "api_key_configured": api_key_configured,
        "default_params": params,
        "source": source,
        "configuration_status": "incomplete" if validation_error else "ready",
        "validation_error": validation_error,
        "restart_required": False,
        "created_at": config.created_at if config else None,
        "updated_at": config.updated_at if config else None,
        "capabilities": {
            key: descriptor[key]
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
        },
    }


def _provider_validation_error(slot: str, provider_name: str) -> str | None:
    try:
        provider_registry.get(ProviderType(slot), provider_name).validate_config()
    except (KeyError, ValueError) as exc:
        return str(exc)
    return None


def _current_model(slot: str, provider_name: str) -> str:
    try:
        return str(provider_registry.get(ProviderType(slot), provider_name).model)
    except Exception:
        names = MODEL_SETTINGS.get((slot, provider_name))
        return str(getattr(settings, names[0])) if names else ""


def _current_base_url(slot: str, provider_name: str) -> str | None:
    setting_name = BASE_URL_SETTINGS.get((slot, provider_name))
    value = getattr(settings, setting_name) if setting_name else None
    return str(value) if value else None


def _current_default_params(slot: str, provider_name: str) -> dict[str, Any]:
    return {
        name: getattr(settings, spec.setting_name)
        for name, spec in _param_specs(slot, provider_name).items()
    }


def _resolve_api_key(api_key_ref: str | None) -> str | None:
    if not api_key_ref or api_key_ref in {DIRECT_API_KEY_REF, NO_API_KEY_REF}:
        return None
    setting_name = _ENV_TO_SETTING.get(api_key_ref)
    if setting_name:
        value = _BASELINE_SETTINGS.get(setting_name)
        return str(value) if value else None
    return os.getenv(api_key_ref)


def resolve_provider_config_api_key(config: ProviderConfig) -> str | None:
    if config.api_key_ref == DIRECT_API_KEY_REF:
        return get_provider_secret(config.provider_type, config.provider_name)
    if config.api_key_ref == NO_API_KEY_REF:
        return None
    return _resolve_api_key(config.api_key_ref or _default_api_key_ref(config.provider_type, config.provider_name))


def baseline_provider_snapshot(slot: str) -> dict[str, Any]:
    """Return the environment-backed provider profile captured before DB overrides."""
    _validate_slot(slot)
    provider_name = str(_BASELINE_SETTINGS[SELECTION_SETTINGS[slot]])
    model_setting_names = MODEL_SETTINGS[(slot, provider_name)]
    preferred_model_setting = (
        model_setting_names[-1]
        if provider_name in {"gemini", "dashscope_image", "qwen_image_musubi", "comfyui_flux"}
        else model_setting_names[0]
    )
    model_name = str(_BASELINE_SETTINGS.get(preferred_model_setting) or "")
    base_setting = BASE_URL_SETTINGS[(slot, provider_name)]
    base_url_value = _BASELINE_SETTINGS.get(base_setting) if base_setting else None
    key_setting = API_KEY_SETTINGS[(slot, provider_name)]
    api_key = _BASELINE_SETTINGS.get(key_setting) if key_setting else None
    if provider_name == "gemini" and not api_key:
        api_key = _BASELINE_SETTINGS.get("image_api_key")
    if provider_name == "dashscope_image" and not api_key:
        api_key = _BASELINE_SETTINGS.get("dashscope_api_key")
    return {
        "provider_name": provider_name,
        "model_name": model_name,
        "base_url": str(base_url_value) if base_url_value else None,
        "api_key": str(api_key) if api_key else None,
        "default_params": {
            name: deepcopy(_BASELINE_SETTINGS.get(spec.setting_name))
            for name, spec in _param_specs(slot, provider_name).items()
        },
    }


def _default_api_key_ref(slot: str, provider_name: str) -> str | None:
    candidates = API_KEY_REF_CANDIDATES.get((slot, provider_name), ())
    for candidate in candidates:
        if _resolve_api_key(candidate):
            return candidate
    return candidates[0] if candidates else None


def _configure_api_key(provider_type: str, payload: ProviderConfigUpsert) -> str | None:
    key_setting = API_KEY_SETTINGS[(provider_type, payload.provider_name)]
    if key_setting is None:
        delete_provider_secret(provider_type, payload.provider_name)
        return None

    mode = payload.api_key_mode
    if mode is None:
        if payload.api_key is not None:
            mode = "direct"
        elif payload.api_key_ref:
            mode = "environment"
        else:
            mode = "environment" if _default_api_key_ref(provider_type, payload.provider_name) else "none"

    if mode == "direct":
        provided_secret = payload.api_key.get_secret_value().strip() if payload.api_key is not None else ""
        if provided_secret:
            set_provider_secret(provider_type, payload.provider_name, provided_secret)
        elif not has_provider_secret(provider_type, payload.provider_name):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="首次使用直接密钥时必须输入 API Key。",
            )
        return DIRECT_API_KEY_REF

    delete_provider_secret(provider_type, payload.provider_name)
    if mode == "none":
        return NO_API_KEY_REF

    api_key_ref = payload.api_key_ref or _default_api_key_ref(provider_type, payload.provider_name)
    if not api_key_ref:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="请选择或填写 API Key 环境变量名。",
        )
    return api_key_ref


def _serialized_api_key_state(
    slot: str,
    provider_name: str,
    stored_ref: str | None,
) -> tuple[str, str | None, bool]:
    if stored_ref == DIRECT_API_KEY_REF:
        return "direct", None, has_provider_secret(slot, provider_name)
    if stored_ref == NO_API_KEY_REF:
        return "none", None, False
    api_key_ref = stored_ref or _default_api_key_ref(slot, provider_name)
    if not api_key_ref:
        return "none", None, False
    return "environment", api_key_ref, bool(_resolve_api_key(api_key_ref))


def _normalize_default_params(
    slot: str,
    provider_name: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    _validate_slot(slot)
    param_specs = _param_specs(slot, provider_name)
    unsupported = sorted(set(params) - set(param_specs))
    if unsupported:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported {slot} default params: {', '.join(unsupported)}",
        )
    normalized: dict[str, Any] = {}
    for name, value in params.items():
        try:
            normalized[name] = param_specs[name].coerce(value)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid {slot} default param {name}: {value!r}",
            ) from exc
    return normalized


def _param_specs(slot: str, provider_name: str) -> dict[str, ParamSpec]:
    return PROVIDER_PARAM_SPECS.get((slot, provider_name), PARAM_SPECS[slot])


def _validate_slot_and_provider(slot: str, provider_name: str) -> None:
    _validate_slot(slot)
    if provider_name not in PROVIDER_CATALOG[slot]:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported provider for {slot}: {provider_name}",
        )


def _validate_slot(slot: str) -> None:
    if slot not in PROVIDER_SLOTS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown provider config slot: {slot}",
        )
