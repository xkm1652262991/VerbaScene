from __future__ import annotations

import mimetypes
import shutil
from pathlib import Path, PurePosixPath
from typing import Protocol
from urllib.parse import unquote, urlparse
from uuid import uuid4

from app.core.config import settings


class MediaStore(Protocol):
    def put_file(
        self,
        project_id: str,
        source: Path,
        *,
        namespace: str,
        filename: str | None = None,
    ) -> str: ...

    def put_bytes(
        self,
        project_id: str,
        content: bytes,
        *,
        namespace: str,
        filename: str,
    ) -> str: ...

    def resolve_local_path(self, uri: str) -> Path: ...

    def delete(self, uri: str) -> None: ...

    def public_uri(self, relative_path: str | Path) -> str: ...


class LocalMediaStore:
    def __init__(self, root: str | Path, public_base_url: str = "/storage") -> None:
        self.root = Path(root).expanduser().resolve()
        self.public_base_url = public_base_url.rstrip("/") or "/storage"
        self.root.mkdir(parents=True, exist_ok=True)

    def put_file(
        self,
        project_id: str,
        source: Path,
        *,
        namespace: str,
        filename: str | None = None,
    ) -> str:
        source_path = source.expanduser().resolve()
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        target = self._project_target(
            project_id,
            namespace,
            filename or f"{uuid4().hex}{source_path.suffix}",
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target)
        return self.public_uri(target.relative_to(self.root))

    def put_bytes(
        self,
        project_id: str,
        content: bytes,
        *,
        namespace: str,
        filename: str,
    ) -> str:
        target = self._project_target(project_id, namespace, filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return self.public_uri(target.relative_to(self.root))

    def resolve_local_path(self, uri: str) -> Path:
        normalized = unquote(uri)
        prefix = self.public_base_url + "/"
        if normalized.startswith(prefix):
            candidate = self.root / normalized[len(prefix) :]
        elif normalized.startswith("/storage/"):
            candidate = self.root / normalized[len("/storage/") :]
        elif normalized.startswith("file://"):
            candidate = Path(urlparse(normalized).path)
        else:
            candidate = Path(normalized).expanduser()
        resolved = candidate.resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise ValueError("Media path is outside the configured storage root")
        if not resolved.is_file():
            raise FileNotFoundError(resolved)
        return resolved

    def delete(self, uri: str) -> None:
        try:
            self.resolve_local_path(uri).unlink(missing_ok=True)
        except (FileNotFoundError, ValueError):
            return

    def public_uri(self, relative_path: str | Path) -> str:
        relative = PurePosixPath(str(relative_path).replace("\\", "/")).as_posix().lstrip("/")
        if relative.startswith("../") or relative == "..":
            raise ValueError("Media path must stay inside the storage root")
        return f"{self.public_base_url}/{relative}"

    def _project_target(
        self,
        project_id: str,
        namespace: str,
        filename: str,
    ) -> Path:
        safe_project_id = _safe_segment(project_id)
        safe_namespace = "/".join(_safe_segment(value) for value in namespace.split("/") if value)
        safe_filename = _safe_segment(filename)
        target = (self.root / "projects" / safe_project_id / safe_namespace / safe_filename).resolve()
        if self.root not in target.parents:
            raise ValueError("Media target escaped the storage root")
        return target


def suffix_for_mime(mime_type: str | None, default: str = ".bin") -> str:
    if not mime_type:
        return default
    return mimetypes.guess_extension(mime_type.split(";", 1)[0].strip()) or default


def _safe_segment(value: str) -> str:
    normalized = value.strip().replace("\\", "_").replace("/", "_")
    if normalized in {"", ".", ".."}:
        raise ValueError("Invalid media path segment")
    return normalized


_media_store: LocalMediaStore | None = None


def get_media_store() -> LocalMediaStore:
    global _media_store
    expected_root = Path(settings.storage_root).expanduser().resolve()
    expected_base = settings.public_storage_base_url.rstrip("/") or "/storage"
    if (
        _media_store is None
        or _media_store.root != expected_root
        or _media_store.public_base_url != expected_base
    ):
        _media_store = LocalMediaStore(expected_root, expected_base)
    return _media_store
