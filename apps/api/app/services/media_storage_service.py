"""Local media storage and data-URI materialization helpers."""

from __future__ import annotations

import base64
from pathlib import Path
from urllib.parse import unquote

from fastapi import HTTPException, status

from app.core.config import settings

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
    base_url = settings.public_storage_base_url.rstrip("/") + "/"
    if not uri.startswith(base_url):
        return
    relative = uri[len(base_url) :]
    path = (Path(settings.storage_root).resolve() / relative).resolve()
    storage_root = Path(settings.storage_root).resolve()
    if storage_root not in path.parents:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return


def local_media_path(uri: str) -> Path:
    storage_root = Path(settings.storage_root).resolve()
    public_prefix = settings.public_storage_base_url.rstrip("/") + "/"
    if uri.startswith(public_prefix):
        path = (storage_root / unquote(uri[len(public_prefix) :])).resolve()
    else:
        path = Path(unquote(uri)).expanduser().resolve()
    if path != storage_root and storage_root not in path.parents:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only media stored inside the project storage root can be processed locally",
        )
    if not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Media input does not exist: {uri}",
        )
    return path


def materialize_data_uri(project_id: str, asset_id: str, uri: str, mime_type: str | None = None) -> str:
    if not uri.startswith("data:") or ";base64," not in uri:
        return uri
    header, payload = uri.split(",", 1)
    detected_mime = mime_type or header.removeprefix("data:").split(";", maxsplit=1)[0] or "application/octet-stream"
    extension = _mime_extension(detected_mime)
    out_dir = Path(settings.storage_root).resolve() / "projects" / project_id / "assets" / "materialized"
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{asset_id}{extension}"
    (out_dir / filename).write_bytes(base64.b64decode(payload))
    return f"{settings.public_storage_base_url}/projects/{project_id}/assets/materialized/{filename}"


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
