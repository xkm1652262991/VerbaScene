import base64
import binascii
import mimetypes
from pathlib import Path
from urllib.request import urlopen

from app.core.config import settings


MAX_REFERENCE_IMAGE_BYTES = 20 * 1024 * 1024


def materialize_reference_data_uris(references: list[str], *, limit: int = 6) -> list[str]:
    materialized: list[str] = []
    for uri in references[:limit]:
        body, mime_type = read_reference_image(uri)
        materialized.append(
            f"data:{mime_type};base64,{base64.b64encode(body).decode('ascii')}"
        )
    return materialized


def read_reference_image(uri: str) -> tuple[bytes, str]:
    if uri.startswith("data:image/") and ";base64," in uri:
        header, encoded = uri.split(",", 1)
        mime_type = header.removeprefix("data:").split(";", maxsplit=1)[0] or "image/png"
        try:
            body = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("Invalid base64 reference image") from exc
        return _validated_image(body, mime_type)

    local_path = _local_storage_uri_path(uri)
    if local_path is not None:
        mime_type = mimetypes.guess_type(local_path.name)[0] or "image/png"
        if local_path.stat().st_size > MAX_REFERENCE_IMAGE_BYTES:
            raise ValueError(f"Reference image exceeds {MAX_REFERENCE_IMAGE_BYTES} bytes")
        return _validated_image(local_path.read_bytes(), mime_type)

    if uri.startswith(("http://", "https://")):
        with urlopen(uri, timeout=settings.provider_timeout_sec) as response:
            body = response.read(MAX_REFERENCE_IMAGE_BYTES + 1)
            mime_type = response.headers.get("Content-Type") or mimetypes.guess_type(uri)[0] or "image/png"
        return _validated_image(body, mime_type.split(";", maxsplit=1)[0])

    raise ValueError(f"Unsupported reference image URI: {uri[:48]}")


def _validated_image(body: bytes, mime_type: str) -> tuple[bytes, str]:
    if len(body) > MAX_REFERENCE_IMAGE_BYTES:
        raise ValueError(f"Reference image exceeds {MAX_REFERENCE_IMAGE_BYTES} bytes")
    if not body:
        raise ValueError("Reference image is empty")
    normalized_mime_type = mime_type.strip().lower()
    if not normalized_mime_type.startswith("image/"):
        raise ValueError(f"Reference payload is not an image: {normalized_mime_type}")
    return body, normalized_mime_type


def _local_storage_uri_path(uri: str) -> Path | None:
    public_base_url = settings.public_storage_base_url.rstrip("/")
    if not public_base_url or not uri.startswith(f"{public_base_url}/"):
        return None
    relative = uri.removeprefix(f"{public_base_url}/").lstrip("/")
    path = (Path(settings.storage_root) / relative).resolve()
    storage_root = Path(settings.storage_root).resolve()
    if storage_root not in (path, *path.parents) or not path.is_file():
        return None
    return path
