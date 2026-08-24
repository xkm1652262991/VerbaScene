import base64
import json
import mimetypes
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import NAMESPACE_URL, uuid5

from app.core.config import settings
from app.providers.base import ProviderAdapter
from app.providers.types import (
    ProviderAsset,
    ProviderError,
    ProviderExecutionMode,
    ProviderRequest,
    ProviderResponse,
    ProviderStatus,
    ProviderType,
    ProviderUsage,
)


_TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}
_RATIO_DIMENSIONS: dict[str, dict[str, tuple[int, int]]] = {
    "480p": {
        "16:9": (864, 496),
        "4:3": (752, 560),
        "1:1": (640, 640),
        "3:4": (560, 752),
        "9:16": (496, 864),
        "21:9": (992, 432),
    },
    "720p": {
        "16:9": (1280, 720),
        "4:3": (1112, 834),
        "1:1": (960, 960),
        "3:4": (834, 1112),
        "9:16": (720, 1280),
        "21:9": (1470, 630),
    },
    "1080p": {
        "16:9": (1920, 1088),
        "4:3": (1664, 1248),
        "1:1": (1440, 1440),
        "3:4": (1248, 1664),
        "9:16": (1088, 1920),
        "21:9": (2176, 928),
    },
}


@dataclass(frozen=True)
class _SeedanceReference:
    media_type: str
    uri: str
    role: str
    asset_id: str | None = None
    reference_role: str | None = None


@dataclass(frozen=True)
class _SeedanceParams:
    duration: int
    resolution: str
    ratio: str
    generate_audio: bool
    watermark: bool


class Seedance2ApiProvider(ProviderAdapter):
    name = "seedance2_api"
    type = ProviderType.VIDEO
    capabilities = [
        "text_to_video",
        "image_to_video",
        "reference_to_video",
        "async_api",
        "poll",
        "polling",
        "cancel",
        "native_audio",
        "multi_reference",
        "smart_duration",
        "seedance2",
    ]
    native_audio = True
    reference_images = True
    reference_videos = True
    reference_audio = True
    multi_reference = True
    smart_duration = True
    min_duration_sec = 4
    max_duration_sec = 15
    supported_resolutions = ["480p", "720p", "1080p"]

    def __init__(self) -> None:
        self.model = _seedance_model()
        self.base_url = settings.seedance2_api_base_url.rstrip("/")
        self.api_key = settings.seedance2_api_key or settings.video_api_key

    def validate_config(self) -> None:
        if not self.base_url:
            raise ValueError(
                "SEEDANCE2_API_BASE_URL is required for Seedance 2.0 provider"
            )
        if not self.api_key:
            raise ValueError(
                "SEEDANCE2_API_KEY, ARK_API_KEY or VIDEO_API_KEY is required "
                "for Seedance 2.0 provider"
            )
        if not self.model:
            raise ValueError("SEEDANCE2_MODEL is required for Seedance 2.0 provider")

    def submit(self, request: ProviderRequest) -> ProviderResponse:
        fallback_task_id = str(
            uuid5(
                NAMESPACE_URL,
                f"{request.project_id}:{request.task_id}:seedance2",
            )
        )
        remote_task_id = fallback_task_id
        try:
            self.validate_config()
            params = _generation_params(request)
            references = _reference_descriptors(request)
            payload = _build_payload(
                request,
                model=self.model,
                params=params,
                references=references,
            )
            submitted = self._request_json(
                f"{self.base_url}/contents/generations/tasks",
                method="POST",
                payload=payload,
            )
            remote_task_id = _task_id(submitted)
            final = self._wait_for_task(remote_task_id)
            status = _provider_status(final)
            if status != ProviderStatus.SUCCEEDED:
                error_code, error_message = _task_error(final)
                return _failed_response(
                    remote_task_id,
                    error_code,
                    error_message,
                    raw_response={
                        "model": self.model,
                        "task": _safe_task_response(final),
                        "request_contract": _request_contract(
                            request,
                            params=params,
                            references=references,
                        ),
                    },
                    retryable=_retryable_error(error_code, status),
                    status=status,
                )

            duration = _result_duration(final, params)
            video_url = _result_video_url(final)
            video_bytes, mime_type = self._download_video(video_url)
            uri = _persist_video_result(
                request,
                remote_task_id,
                video_bytes,
                mime_type,
            )
            width, height = _result_dimensions(final, params)
            asset = ProviderAsset(
                asset_type="video",
                uri=uri,
                mime_type=mime_type,
                width=width,
                height=height,
                duration_sec=duration,
                metadata={
                    "provider_task_id": remote_task_id,
                    "remote_video_url": video_url,
                    "native_audio": params.generate_audio,
                    "resolution": str(final.get("resolution") or params.resolution),
                    "ratio": str(final.get("ratio") or params.ratio),
                    "duration_mode": _duration_mode(params.duration),
                    "reference_count": len(references),
                },
            )
            return ProviderResponse(
                status=ProviderStatus.SUCCEEDED,
                provider_task_id=remote_task_id,
                execution_mode=ProviderExecutionMode.ASYNC,
                assets=[asset],
                raw_response={
                    "model": self.model,
                    "task": _safe_task_response(final),
                    "request_contract": _request_contract(
                        request,
                        params=params,
                        references=references,
                    ),
                },
                usage=ProviderUsage(cost=Decimal("0"), unit="CNY"),
            )
        except (
            HTTPError,
            URLError,
            TimeoutError,
            OSError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            message, raw_error, code, retryable = _exception_details(exc)
            return _failed_response(
                remote_task_id,
                code,
                message,
                raw_response=raw_error,
                retryable=retryable,
                status=(
                    ProviderStatus.TIMEOUT
                    if isinstance(exc, TimeoutError)
                    else ProviderStatus.FAILED
                ),
            )

    def poll(self, provider_task_id: str) -> ProviderResponse:
        self.validate_config()
        task = self._get_task(provider_task_id)
        return self.normalize_response(task)

    def cancel(self, provider_task_id: str) -> None:
        self.validate_config()
        self._request_json(
            f"{self.base_url}/contents/generations/tasks/{provider_task_id}",
            method="DELETE",
        )

    def normalize_response(self, raw_response: dict) -> ProviderResponse:
        provider_task_id = str(raw_response.get("id") or "")
        status = _provider_status(raw_response)
        assets: list[ProviderAsset] = []
        error: ProviderError | None = None
        if status == ProviderStatus.SUCCEEDED:
            video_url = _result_video_url(raw_response)
            duration = _response_duration(raw_response)
            params = _SeedanceParams(
                duration=int(duration),
                resolution=str(raw_response.get("resolution") or "480p"),
                ratio=str(raw_response.get("ratio") or "16:9"),
                generate_audio=True,
                watermark=False,
            )
            width, height = _result_dimensions(raw_response, params)
            assets.append(
                ProviderAsset(
                    asset_type="video",
                    uri=video_url,
                    mime_type="video/mp4",
                    width=width,
                    height=height,
                    duration_sec=duration,
                    metadata={"provider_task_id": provider_task_id, "remote": True},
                )
            )
        elif status in {
            ProviderStatus.FAILED,
            ProviderStatus.CANCELLED,
            ProviderStatus.TIMEOUT,
        }:
            code, message = _task_error(raw_response)
            error = ProviderError(
                error_code=code,
                error_message=message,
                is_retryable=_retryable_error(code, status),
                raw_error=_safe_task_response(raw_response),
            )
        return ProviderResponse(
            status=status,
            provider_task_id=provider_task_id,
            execution_mode=ProviderExecutionMode.ASYNC,
            poll_after_sec=(
                settings.seedance2_api_poll_interval_sec
                if status in {ProviderStatus.QUEUED, ProviderStatus.RUNNING}
                else None
            ),
            assets=assets,
            raw_response=_safe_task_response(raw_response),
            usage=ProviderUsage(cost=Decimal("0"), unit="CNY"),
            error=error,
        )

    def _wait_for_task(self, task_id: str) -> dict:
        deadline = time.monotonic() + settings.seedance2_api_job_timeout_sec
        while True:
            task = self._get_task(task_id)
            status = str(task.get("status") or "").strip().lower()
            if status in _TERMINAL_STATUSES:
                return task
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Seedance 2.0 task timed out: {task_id}")
            time.sleep(settings.seedance2_api_poll_interval_sec)

    def _get_task(self, task_id: str) -> dict:
        return self._request_json(
            f"{self.base_url}/contents/generations/tasks/{task_id}",
            method="GET",
        )

    def _request_json(
        self,
        url: str,
        *,
        method: str,
        payload: dict | None = None,
    ) -> dict:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method=method,
        )
        with urlopen(
            request,
            timeout=settings.seedance2_api_timeout_sec,
        ) as response:
            body = response.read()
        if not body:
            return {}
        parsed = json.loads(body.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("Seedance 2.0 API returned a non-object response")
        return parsed

    def _download_video(self, video_url: str) -> tuple[bytes, str]:
        parsed = urlparse(video_url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("Seedance 2.0 result video URL must use HTTP(S)")
        with urlopen(
            Request(video_url, method="GET"),
            timeout=settings.provider_timeout_sec,
        ) as response:
            body = response.read()
            content_type = response.headers.get("Content-Type", "video/mp4")
        mime_type = content_type.split(";", 1)[0].strip().lower()
        if not mime_type.startswith("video/"):
            mime_type = "video/mp4"
        return body, mime_type


def _generation_params(request: ProviderRequest) -> _SeedanceParams:
    duration_value = request.params.get(
        "duration",
        request.params.get("duration_sec", -1),
    )
    duration_float = float(duration_value)
    if not duration_float.is_integer():
        raise ValueError("Seedance 2.0 duration must be a whole number of seconds")
    duration = int(duration_float)
    if duration != -1 and not 4 <= duration <= 15:
        raise ValueError("Seedance 2.0 duration must be -1 or between 4 and 15 seconds")

    resolution = str(
        request.params.get("resolution")
        or settings.seedance2_resolution
        or "480p"
    ).strip().lower()
    if resolution not in {"480p", "720p", "1080p"}:
        raise ValueError("Seedance 2.0 resolution must be 480p, 720p, or 1080p")

    ratio = str(
        request.params.get("ratio")
        or settings.seedance2_ratio
        or "16:9"
    ).strip().lower()
    if ratio not in {"16:9", "4:3", "1:1", "3:4", "9:16", "21:9", "adaptive"}:
        raise ValueError("Unsupported Seedance 2.0 ratio")

    return _SeedanceParams(
        duration=duration,
        resolution=resolution,
        ratio=ratio,
        generate_audio=_as_bool(
            request.params.get(
                "generate_audio",
                settings.seedance2_generate_audio,
            )
        ),
        watermark=_as_bool(
            request.params.get("watermark", settings.seedance2_watermark)
        ),
    )


def _build_payload(
    request: ProviderRequest,
    *,
    model: str,
    params: _SeedanceParams,
    references: list[_SeedanceReference],
) -> dict:
    content: list[dict] = [{"type": "text", "text": request.prompt}]
    for reference in references:
        url = _reference_url(reference)
        content.append(
            {
                "type": f"{reference.media_type}_url",
                f"{reference.media_type}_url": {"url": url},
                "role": reference.role,
            }
        )
    return {
        "model": model,
        "content": content,
        "generate_audio": params.generate_audio,
        "resolution": params.resolution,
        "ratio": params.ratio,
        "duration": params.duration,
        "watermark": params.watermark,
    }


def _reference_descriptors(request: ProviderRequest) -> list[_SeedanceReference]:
    resolution = request.metadata.get("asset_resolution")
    raw_references = (
        resolution.get("reference_assets")
        if isinstance(resolution, dict)
        else None
    )
    metadata_items = [
        item
        for item in (raw_references or [])
        if isinstance(item, dict)
    ]
    descriptors: list[_SeedanceReference] = []
    used_indexes: set[int] = set()
    for uri in request.references:
        matched: dict | None = None
        for index, item in enumerate(metadata_items):
            if index in used_indexes:
                continue
            if str(item.get("uri") or "") == uri:
                used_indexes.add(index)
                matched = item
                break
        media_type = _media_type(
            str(matched.get("asset_type") or "") if matched else "",
            uri,
        )
        descriptors.append(
            _SeedanceReference(
                media_type=media_type,
                uri=uri,
                role=f"reference_{media_type}",
                asset_id=(
                    str(matched.get("asset_id"))
                    if matched and matched.get("asset_id")
                    else None
                ),
                reference_role=(
                    str(matched.get("reference_role"))
                    if matched and matched.get("reference_role")
                    else None
                ),
            )
        )
    return descriptors


def _media_type(asset_type: str, uri: str) -> str:
    normalized = asset_type.strip().lower()
    if normalized in {"image", "video", "audio"}:
        return normalized
    mime_type = mimetypes.guess_type(urlparse(uri).path)[0] or ""
    if uri.startswith("data:"):
        mime_type = uri[5:].split(";", 1)[0]
    for candidate in ("image", "video", "audio"):
        if mime_type.startswith(f"{candidate}/"):
            return candidate
    return "image"


def _reference_url(reference: _SeedanceReference) -> str:
    uri = reference.uri.strip()
    if reference.media_type == "image":
        return _image_reference_url(uri)
    if uri.startswith("asset://"):
        return uri
    if uri.startswith(("http://", "https://")) and not _is_loopback_url(uri):
        return uri
    raise ValueError(
        f"Seedance 2.0 local {reference.media_type} references require a "
        "public HTTP(S) URL or asset:// URI"
    )


def _image_reference_url(uri: str) -> str:
    if uri.startswith("data:image/") or uri.startswith("asset://"):
        return uri
    if uri.startswith(("http://", "https://")):
        if not _is_loopback_url(uri):
            return uri
        local_path = _local_storage_path(uri)
        if local_path is not None:
            return _image_data_url(local_path)
        with urlopen(uri, timeout=settings.provider_timeout_sec) as response:
            body = response.read()
            mime_type = (
                response.headers.get("Content-Type")
                or mimetypes.guess_type(urlparse(uri).path)[0]
                or "image/png"
            )
        return _bytes_data_url(body, mime_type)
    local_path = _local_storage_path(uri)
    if local_path is not None:
        return _image_data_url(local_path)
    raise ValueError(
        "Seedance 2.0 image references must use HTTP(S), asset://, "
        "Base64 Data URL, or project storage"
    )


def _local_storage_path(uri: str) -> Path | None:
    storage_root = Path(settings.storage_root).resolve()
    public_base = settings.public_storage_base_url.rstrip("/")
    relative: str | None = None
    if public_base and uri.startswith(public_base + "/"):
        relative = uri[len(public_base) + 1 :]
    elif uri.startswith("/storage/"):
        relative = uri[len("/storage/") :]
    elif not urlparse(uri).scheme:
        candidate = Path(uri).expanduser()
        if candidate.is_absolute():
            resolved = candidate.resolve()
            if resolved.is_relative_to(storage_root) and resolved.is_file():
                return resolved
        relative = uri.lstrip("/")
    if relative is None:
        return None
    resolved = (storage_root / relative).resolve()
    if not resolved.is_relative_to(storage_root) or not resolved.is_file():
        return None
    return resolved


def _image_data_url(path: Path) -> str:
    mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
    if not mime_type.startswith("image/"):
        raise ValueError("Seedance 2.0 project reference is not an image")
    return _bytes_data_url(path.read_bytes(), mime_type)


def _bytes_data_url(body: bytes, mime_type: str) -> str:
    normalized = mime_type.split(";", 1)[0].strip().lower()
    if not normalized.startswith("image/"):
        raise ValueError("Seedance 2.0 image reference did not return an image")
    return (
        f"data:{normalized};base64,"
        f"{base64.b64encode(body).decode('ascii')}"
    )


def _is_loopback_url(uri: str) -> bool:
    host = (urlparse(uri).hostname or "").strip().lower()
    return host in {"localhost", "127.0.0.1", "::1"}


def _request_contract(
    request: ProviderRequest,
    *,
    params: _SeedanceParams,
    references: list[_SeedanceReference],
) -> dict:
    counts = {
        media_type: sum(
            1 for item in references if item.media_type == media_type
        )
        for media_type in ("image", "video", "audio")
    }
    return {
        "prompt": request.prompt,
        "duration_mode": _duration_mode(params.duration),
        "duration": params.duration,
        "resolution": params.resolution,
        "ratio": params.ratio,
        "generate_audio": params.generate_audio,
        "watermark": params.watermark,
        "reference_counts": counts,
        "reference_asset_ids": [
            item.asset_id for item in references if item.asset_id
        ],
    }


def _task_id(payload: dict) -> str:
    value = payload.get("id") or payload.get("task_id")
    if not value:
        raise ValueError("Seedance 2.0 response did not include a task ID")
    return str(value)


def _provider_status(payload: dict) -> ProviderStatus:
    value = str(payload.get("status") or "").strip().lower()
    mapping = {
        "queued": ProviderStatus.QUEUED,
        "running": ProviderStatus.RUNNING,
        "succeeded": ProviderStatus.SUCCEEDED,
        "failed": ProviderStatus.FAILED,
        "cancelled": ProviderStatus.CANCELLED,
        "canceled": ProviderStatus.CANCELLED,
    }
    return mapping.get(value, ProviderStatus.RUNNING)


def _task_error(payload: dict) -> tuple[str, str]:
    error = payload.get("error")
    if isinstance(error, dict):
        return (
            str(error.get("code") or "seedance2_task_failed"),
            str(error.get("message") or error.get("code") or "Seedance 2.0 task failed"),
        )
    return "seedance2_task_failed", str(
        error or payload.get("message") or "Seedance 2.0 task failed"
    )


def _result_video_url(payload: dict) -> str:
    content = payload.get("content")
    video_url = content.get("video_url") if isinstance(content, dict) else None
    if not isinstance(video_url, str) or not video_url.strip():
        raise ValueError("Seedance 2.0 task did not include content.video_url")
    return video_url.strip()


def _duration_mode(duration: int) -> str:
    return "provider_auto" if duration == -1 else "fixed"


def _response_duration(payload: dict) -> Decimal:
    value = payload.get("duration")
    try:
        duration = Decimal(str(value)).quantize(Decimal("0.001"))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Seedance 2.0 response did not include actual duration") from exc
    if duration <= 0:
        raise ValueError("Seedance 2.0 response included an invalid actual duration")
    return duration


def _result_duration(payload: dict, params: _SeedanceParams) -> Decimal:
    if payload.get("duration") is not None:
        return _response_duration(payload)
    if params.duration == -1:
        raise ValueError("Seedance 2.0 smart-duration response omitted actual duration")
    return Decimal(str(params.duration)).quantize(Decimal("0.001"))


def _result_dimensions(
    payload: dict,
    params: _SeedanceParams,
) -> tuple[int, int]:
    resolution = str(payload.get("resolution") or params.resolution).lower()
    ratio = str(payload.get("ratio") or params.ratio).lower()
    if ratio == "adaptive":
        return 0, 0
    return _RATIO_DIMENSIONS.get(resolution, {}).get(ratio, (0, 0))


def _persist_video_result(
    request: ProviderRequest,
    task_id: str,
    video_bytes: bytes,
    mime_type: str,
) -> str:
    suffix = mimetypes.guess_extension(mime_type) or ".mp4"
    storage_dir = (
        Path(settings.storage_root).resolve()
        / "projects"
        / request.project_id
        / "provider-results"
        / "seedance2"
    )
    storage_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{task_id}{suffix}"
    path = storage_dir / filename
    path.write_bytes(video_bytes)
    return (
        f"{settings.public_storage_base_url}/projects/{request.project_id}"
        f"/provider-results/seedance2/{filename}"
    )


def _safe_task_response(payload: dict) -> dict:
    return {
        key: value
        for key, value in payload.items()
        if key in {
            "id",
            "model",
            "status",
            "error",
            "created_at",
            "updated_at",
            "content",
            "seed",
            "resolution",
            "ratio",
            "duration",
            "frames",
            "framespersecond",
            "usage",
        }
    }


def _failed_response(
    provider_task_id: str,
    code: str,
    message: str,
    *,
    raw_response: dict,
    retryable: bool,
    status: ProviderStatus,
) -> ProviderResponse:
    return ProviderResponse(
        status=status,
        provider_task_id=provider_task_id,
        execution_mode=ProviderExecutionMode.ASYNC,
        raw_response=raw_response,
        error=ProviderError(
            error_code=code,
            error_message=message,
            is_retryable=retryable,
            raw_error=raw_response,
        ),
        usage=ProviderUsage(cost=Decimal("0"), unit="CNY"),
    )


def _exception_details(exc: Exception) -> tuple[str, dict, str, bool]:
    if isinstance(exc, HTTPError):
        raw_text = exc.read().decode("utf-8", errors="replace")
        try:
            raw = json.loads(raw_text)
        except json.JSONDecodeError:
            raw = {"raw_text": raw_text, "status_code": exc.code}
        error = raw.get("error") if isinstance(raw, dict) else None
        if isinstance(error, dict):
            code = str(error.get("code") or f"seedance2_http_{exc.code}")
            message = str(error.get("message") or code)
        else:
            code = f"seedance2_http_{exc.code}"
            message = str(
                (raw.get("message") if isinstance(raw, dict) else None)
                or raw_text
                or exc
            )
        retryable = exc.code in {408, 409, 425, 429} or exc.code >= 500
        return message, raw if isinstance(raw, dict) else {}, code, retryable
    if isinstance(exc, TimeoutError):
        return str(exc), {}, "seedance2_timeout", True
    if isinstance(exc, (URLError, OSError)):
        return str(exc), {}, "seedance2_transport_failed", True
    return str(exc), {}, "seedance2_invalid_request", False


def _retryable_error(code: str, status: ProviderStatus) -> bool:
    normalized = code.lower()
    if status == ProviderStatus.CANCELLED:
        return False
    return (
        "quota" in normalized
        or "rate" in normalized
        or "timeout" in normalized
        or "internal" in normalized
        or "serviceunavailable" in normalized
    )


def _as_bool(value: object) -> bool:
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
    raise ValueError(f"Invalid boolean value: {value!r}")


def _seedance_model() -> str:
    if settings.video_provider == "seedance2_api" and settings.video_model not in {
        "",
        "mock-video",
    }:
        return settings.video_model
    return settings.seedance2_model


def build_seedance2_providers() -> list[ProviderAdapter]:
    return [Seedance2ApiProvider()]
