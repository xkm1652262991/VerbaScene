from decimal import Decimal

from app.models import Asset, Shot


def build_export_manifest(
    *,
    shots: list[Shot],
    video_assets: list[Asset],
    subtitle_mode: str = "none",
) -> dict:
    return {
        "shots": [
            {
                "shot_id": shot.id,
                "shot_no": shot.shot_no,
                "duration_sec": str(shot.duration_sec or Decimal("0")),
                "video_asset_id": _find_asset_for_entity(video_assets, shot.id),
            }
            for shot in shots
        ],
        "audio_policy": "preserve_native_or_inject_silence",
        "subtitle_mode": subtitle_mode,
    }


def total_duration(shots: list[Shot]) -> Decimal:
    total = sum((shot.duration_sec or Decimal("0") for shot in shots), Decimal("0"))
    return total.quantize(Decimal("0.001"))


def _find_asset_for_entity(assets: list[Asset], entity_id: str) -> str | None:
    for asset in assets:
        if asset.entity_id == entity_id:
            return asset.id
    return None
