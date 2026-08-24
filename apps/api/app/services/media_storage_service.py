"""Local media storage and data-URI materialization helpers."""

from __future__ import annotations

import base64
from pathlib import Path

from fastapi import HTTPException, status

from app.platform.media import get_media_store

def safe_image_extension(filename: str, content_type: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return suffix
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }.get(content_type, ".png")


def read_image_size(path: Path) -> tuple[int | None, int | None]:
    try:
        from PIL import Image

        with Image.open(path) as image:
            return image.width, image.height
    except Exception:
        return None, None


def unlink_local_storage_file(uri: str) -> None:
    get_media_store().delete(uri)


def local_media_path(uri: str) -> Path:
    try:
        return get_media_store().resolve_local_path(uri)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only media stored inside the project storage root can be processed locally",
        ) from exc
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Media input does not exist: {uri}",
        ) from exc


def materialize_data_uri(project_id: str, asset_id: str, uri: str, mime_type: str | None = None) -> str:
    if not uri.startswith("data:") or ";base64," not in uri:
        return uri
    header, payload = uri.split(",", 1)
    detected_mime = mime_type or header.removeprefix("data:").split(";", maxsplit=1)[0] or "application/octet-stream"
    extension = _mime_extension(detected_mime)
    filename = f"{asset_id}{extension}"
    return get_media_store().put_bytes(
        project_id,
        base64.b64decode(payload),
        namespace="assets/materialized",
        filename=filename,
    )


def _mime_extension(mime_type: str) -> str:
    mapping = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "video/mp4": ".mp4",
        "audio/mpeg": ".mp3",
        "audio/wav": ".wav",
    }
    return mapping.get(mime_type.lower(), ".bin")
