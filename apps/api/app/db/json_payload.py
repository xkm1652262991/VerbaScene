from __future__ import annotations

from typing import Any


MAX_INLINE_BINARY_CHARS = 16_384
BASE64_FIELD_NAMES = {
    "audio_base64",
    "b64_json",
    "base64",
    "file_base64",
    "image_base64",
    "video_base64",
}


def compact_json_payload(value: Any, *, field_name: str | None = None) -> Any:
    if isinstance(value, dict):
        return {
            key: compact_json_payload(item, field_name=str(key))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [compact_json_payload(item, field_name=field_name) for item in value]
    if not isinstance(value, str) or len(value) <= MAX_INLINE_BINARY_CHARS:
        return value

    normalized_name = (field_name or "").lower()
    if value.startswith("data:") and ";base64," in value[:256]:
        header = value.split(",", 1)[0]
        return f"{header},<omitted inline binary: {len(value)} chars>"
    if normalized_name in BASE64_FIELD_NAMES:
        return f"<omitted inline binary: {len(value)} chars>"
    return value
