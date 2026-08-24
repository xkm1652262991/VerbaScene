from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from threading import RLock

from app.core.config import settings


_STORE_LOCK = RLock()
_API_ROOT = Path(__file__).resolve().parents[2]


class ProviderSecretStoreError(RuntimeError):
    pass


def get_provider_secret(provider_type: str, provider_name: str) -> str | None:
    with _STORE_LOCK:
        return _read_secrets().get(_secret_key(provider_type, provider_name))


def has_provider_secret(provider_type: str, provider_name: str) -> bool:
    return bool(get_provider_secret(provider_type, provider_name))


def set_provider_secret(provider_type: str, provider_name: str, secret: str) -> None:
    normalized = secret.strip()
    if not normalized:
        raise ValueError("Provider API key cannot be empty")
    with _STORE_LOCK:
        secrets = _read_secrets()
        secrets[_secret_key(provider_type, provider_name)] = normalized
        _write_secrets(secrets)


def delete_provider_secret(provider_type: str, provider_name: str) -> None:
    with _STORE_LOCK:
        secrets = _read_secrets()
        if secrets.pop(_secret_key(provider_type, provider_name), None) is not None:
            _write_secrets(secrets)


def provider_secret_file_path() -> Path:
    configured = Path(settings.provider_secret_file).expanduser()
    if configured.is_absolute():
        return configured.resolve()
    return (_API_ROOT / configured).resolve()


def _secret_key(provider_type: str, provider_name: str) -> str:
    return f"{provider_type}:{provider_name}"


def _read_secrets() -> dict[str, str]:
    path = provider_secret_file_path()
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProviderSecretStoreError(f"Provider secret file is unreadable: {path}") from exc
    raw_secrets = payload.get("secrets") if isinstance(payload, dict) else None
    if not isinstance(raw_secrets, dict):
        raise ProviderSecretStoreError(f"Provider secret file has an invalid structure: {path}")
    return {
        str(key): str(value)
        for key, value in raw_secrets.items()
        if isinstance(key, str) and isinstance(value, str) and value
    }


def _write_secrets(secrets: dict[str, str]) -> None:
    path = provider_secret_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        descriptor, raw_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary_path = Path(raw_path)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"version": 1, "secrets": secrets}, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        path.chmod(0o600)
    except OSError as exc:
        raise ProviderSecretStoreError(f"Provider secret file cannot be written: {path}") from exc
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
