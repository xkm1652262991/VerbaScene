from typing import Any


_MODELS_WITH_FIXED_SAMPLING = frozenset({"kimi-k3", "kimi/kimi-k3"})


def structured_json_params(*, temperature: float) -> dict[str, Any]:
    """Build the shared request contract for model-authored JSON artifacts."""
    return {
        "temperature": temperature,
        "response_format": {"type": "json_object"},
    }


def add_temperature_if_supported(
    payload: dict[str, Any],
    *,
    model: str,
    params: dict[str, Any],
    default: float = 0.7,
) -> None:
    """Add temperature unless the selected model owns its sampling policy."""
    if model.strip().lower() in _MODELS_WITH_FIXED_SAMPLING:
        return
    payload["temperature"] = params.get("temperature", default)
