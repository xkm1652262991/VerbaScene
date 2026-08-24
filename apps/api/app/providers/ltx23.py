import base64
import json
import mimetypes
import tempfile
import time
from dataclasses import dataclass
from decimal import Decimal
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
    SubmissionState,
)


class LTX23ApiProvider(ProviderAdapter):
    name = "ltx23_api"
    type = ProviderType.VIDEO
    capabilities = [
        "text_to_video",
        "image_to_video",
        "reference_to_video",
        "async_api",
        "poll",
        "polling",
        "cancel",
        "ltx23",
    ]
    reference_images = True
    max_duration_sec = 5
    supported_resolutions = ["1024x576"]

    def __init__(self) -> None:
        self.model = _ltx23_model()
        self.base_url = settings.ltx23_api_base_url.rstrip("/")

    def validate_config(self) -> None:
        if not self.base_url:
            raise ValueError("LTX23_API_BASE_URL is required for LTX-2.3 API provider")

    def submit(self, request: ProviderRequest) -> ProviderResponse:
        provider_task_id = str(
            uuid5(NAMESPACE_URL, f"{request.project_id}:{request.task_id}:ltx23")
        )
        submission_state = SubmissionState.NOT_SUBMITTED
        try:
            self.validate_config()
            if request.references:
                image_input = _primary_image(request)
                _generation_params(request)
                submission_state = SubmissionState.UNKNOWN
                job = self._submit_image_job(
                    request,
                    client_job_id=provider_task_id,
                    image_input=image_input,
                )
                generation_mode = "image_to_video"
            else:
                _generation_params(request)
                submission_state = SubmissionState.UNKNOWN
                job = self._submit_text_job(
                    request,
                    client_job_id=provider_task_id,
                )
                generation_mode = "text_to_video"

            job_id = _job_id(job)
            provider_task_id = job_id
            submission_state = SubmissionState.ACCEPTED
        except (
            HTTPError,
            URLError,
            TimeoutError,
            OSError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            message, raw_error = _exception_details(exc)
            return _failed_response(
                provider_task_id,
                "ltx23_api_failed",
                message,
                raw_response=raw_error,
                retryable=True,
                submission_state=(
                    SubmissionState.NOT_SUBMITTED
                    if isinstance(exc, HTTPError)
                    else submission_state
                ),
            )
        return ProviderResponse(
            status=_provider_status(job),
            provider_task_id=provider_task_id,
            execution_mode=ProviderExecutionMode.ASYNC,
            poll_after_sec=settings.ltx23_api_poll_interval_sec,
            raw_response={
                "model": self.model,
                "ltx23_job_id": job_id,
                "generation_mode": generation_mode,
                "job": job,
            },
            usage=ProviderUsage(cost=Decimal("0"), unit="USD"),
        )

    def poll(self, provider_task_id: str) -> ProviderResponse:
        self.validate_config()
        status_payload = self._get_job(provider_task_id)
        status = _provider_status(status_payload)
        error = None
        if status in {ProviderStatus.FAILED, ProviderStatus.CANCELLED, ProviderStatus.TIMEOUT}:
            error = ProviderError(
                error_code="ltx23_job_failed",
                error_message=_job_error_message(status_payload),
                is_retryable=False,
                submission_state=SubmissionState.ACCEPTED,
                raw_error=status_payload,
            )
        return ProviderResponse(
            status=status,
            provider_task_id=provider_task_id,
            execution_mode=ProviderExecutionMode.ASYNC,
            poll_after_sec=(
                settings.ltx23_api_poll_interval_sec
                if status in {ProviderStatus.QUEUED, ProviderStatus.RUNNING}
                else None
            ),
            raw_response=status_payload,
            error=error,
        )

    def fetch_result(
        self,
        provider_task_id: str,
        *,
        request: ProviderRequest | None = None,
        provider_context: dict | None = None,
    ) -> ProviderResponse:
        _ = provider_context
        polled = self.poll(provider_task_id)
        if polled.status != ProviderStatus.SUCCEEDED:
            return polled
        if request is None:
            raise ValueError("LTX-2.3 fetch_result requires the original request snapshot")
        video_bytes, content_type = self._download_result(provider_task_id)
        mime_type = _video_mime_type(content_type)
        uri = _temporary_video_result(provider_task_id, video_bytes, mime_type)
        width, height = _result_dimensions(request, polled.raw_response)
        duration = Decimal(
            str(
                _status_value(polled.raw_response, "duration_sec", "duration")
                or request.params.get("duration_sec")
                or _duration_from_params(request)
            )
        ).quantize(Decimal("0.001"))
        return ProviderResponse(
            status=ProviderStatus.SUCCEEDED,
            provider_task_id=provider_task_id,
            execution_mode=ProviderExecutionMode.ASYNC,
            assets=[
                ProviderAsset(
                    asset_type="video",
                    uri=uri,
                    mime_type=mime_type,
                    width=width,
                    height=height,
                    duration_sec=duration,
                    metadata={
                        "provider_task_id": provider_task_id,
                        "ltx23_job_id": provider_task_id,
                        "temporary_file": True,
                    },
                )
            ],
            raw_response=polled.raw_response,
            usage=ProviderUsage(cost=Decimal("0"), unit="USD"),
        )

    def cancel(self, provider_task_id: str) -> None:
        self.validate_config()
        _urlopen_json(
            Request(
                f"{self.base_url}/v1/ltx23/jobs/{provider_task_id}",
                method="DELETE",
            ),
            timeout=settings.ltx23_api_timeout_sec,
        )

    def _submit_text_job(
        self,
        request: ProviderRequest,
        *,
        client_job_id: str,
    ) -> dict:
        params = _generation_params(request)
        payload: dict[str, object] = {
            "prompt": request.prompt,
            "width": params.width,
            "height": params.height,
            "frames": params.frames,
            "fps": params.fps,
            "steps": params.steps,
            "cfg": params.cfg,
            "client_job_id": client_job_id,
        }
        if request.negative_prompt:
            payload["negative"] = request.negative_prompt
        if params.seed is not None:
            payload["seed"] = params.seed
        http_request = Request(
            f"{self.base_url}/v1/ltx23/videos/text",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return _urlopen_json(
            http_request,
            timeout=settings.ltx23_api_timeout_sec,
        )

    def _submit_image_job(
        self,
        request: ProviderRequest,
        *,
        client_job_id: str,
        image_input: "_LTX23ImageInput",
    ) -> dict:
        params = _generation_params(request)
        fields: dict[str, object] = {
            "prompt": request.prompt,
            "width": params.width,
            "height": params.height,
            "frames": params.frames,
            "fps": params.fps,
            "steps": params.steps,
            "cfg": params.cfg,
            "strength": params.strength,
            "client_job_id": client_job_id,
        }
        if request.negative_prompt:
            fields["negative"] = request.negative_prompt
        if params.seed is not None:
            fields["seed"] = params.seed
        body, content_type = _multipart_body(
            fields,
            file_field="image",
            filename=image_input.image_name,
            file_bytes=image_input.image_bytes,
            file_mime=image_input.image_mime,
        )
        http_request = Request(
            f"{self.base_url}/v1/ltx23/videos/image",
            data=body,
            headers={"Content-Type": content_type},
            method="POST",
        )
        return _urlopen_json(
            http_request,
            timeout=settings.ltx23_api_timeout_sec,
        )

    def _get_job(self, job_id: str) -> dict:
        return _urlopen_json(
            Request(
                f"{self.base_url}/v1/ltx23/jobs/{job_id}",
                method="GET",
            ),
            timeout=settings.ltx23_api_timeout_sec,
        )

    def _download_result(self, job_id: str) -> tuple[bytes, str]:
        http_request = Request(
            f"{self.base_url}/v1/ltx23/jobs/{job_id}/result",
            method="GET",
        )
        with urlopen(
            http_request,
            timeout=settings.provider_timeout_sec,
        ) as response:
            content_type = response.headers.get(
                "Content-Type",
                "video/mp4",
            )
            body = response.read()
        if "json" not in content_type.lower():
            return body, content_type
        return self._download_json_result(
            json.loads(body.decode("utf-8")),
        )

    def _download_json_result(self, payload: dict) -> tuple[bytes, str]:
        data = _data(payload)
        encoded = (
            data.get("video_base64")
            or data.get("b64_json")
            or data.get("base64")
        )
        if isinstance(encoded, str) and encoded:
            return base64.b64decode(encoded), str(
                data.get("content_type") or data.get("mime_type") or "video/mp4"
            )

        result_url = (
            data.get("video_url")
            or data.get("result_url")
            or data.get("url")
        )
        if not isinstance(result_url, str) or not result_url:
            raise ValueError("LTX-2.3 result response did not include video bytes or URL")
        if result_url.startswith("/"):
            result_url = f"{self.base_url}{result_url}"
        elif not result_url.startswith(("http://", "https://")):
            raise ValueError("LTX-2.3 result URL must be HTTP(S) or service-relative")
        with urlopen(
            Request(result_url, method="GET"),
            timeout=settings.provider_timeout_sec,
        ) as response:
            return response.read(), response.headers.get(
                "Content-Type",
                "video/mp4",
            )


@dataclass(frozen=True)
class _LTX23GenerationParams:
    width: int
    height: int
    frames: int
    fps: float
    steps: int
    cfg: float
    strength: float
    seed: int | None


@dataclass(frozen=True)
class _LTX23ImageInput:
    image_name: str
    image_bytes: bytes
    image_mime: str


def _generation_params(request: ProviderRequest) -> _LTX23GenerationParams:
    width, height = _video_dimensions(request)
    frames = int(request.params.get("frames", settings.ltx23_frames))
    fps = float(request.params.get("fps", settings.ltx23_fps))
    steps = int(request.params.get("steps", settings.ltx23_steps))
    cfg = float(
        request.params.get(
            "cfg",
            request.params.get("guidance", settings.ltx23_cfg),
        )
    )
    strength = float(
        request.params.get("strength", settings.ltx23_strength)
    )
    seed_value = request.params.get("seed", settings.ltx23_seed)
    seed = (
        None
        if seed_value is None
        or (isinstance(seed_value, str) and not seed_value.strip())
        else int(seed_value)
    )
    if width < 1 or height < 1:
        raise ValueError("LTX-2.3 width and height must be positive integers")
    if frames < 1 or fps <= 0 or steps < 1:
        raise ValueError("LTX-2.3 frames, fps, and steps must be positive")
    if cfg < 0:
        raise ValueError("LTX-2.3 cfg must be non-negative")
    if not 0 <= strength <= 1:
        raise ValueError("LTX-2.3 strength must be between 0 and 1")
    return _LTX23GenerationParams(
        width=width,
        height=height,
        frames=frames,
        fps=fps,
        steps=steps,
        cfg=cfg,
        strength=strength,
        seed=seed,
    )


def _video_dimensions(request: ProviderRequest) -> tuple[int, int]:
    width_value = request.params.get("width")
    height_value = request.params.get("height")
    if width_value is not None or height_value is not None:
        if width_value is None or height_value is None:
            raise ValueError("LTX-2.3 width and height must be provided together")
        return int(width_value), int(height_value)

    size = str(request.params.get("size") or settings.ltx23_size or "1024x576")
    normalized = size.lower().replace("*", "x")
    if "x" not in normalized:
        raise ValueError("LTX-2.3 size must use WIDTHxHEIGHT")
    width, height = normalized.split("x", 1)
    return int(width), int(height)


def _primary_image(request: ProviderRequest) -> _LTX23ImageInput:
    uri = request.references[0]
    if uri.startswith("data:"):
        header, encoded = uri.split(",", 1)
        mime_type = header.split(":", 1)[1].split(";", 1)[0] or "image/png"
        return _LTX23ImageInput(
            image_name=f"{request.task_id}{mimetypes.guess_extension(mime_type) or '.png'}",
            image_bytes=base64.b64decode(encoded),
            image_mime=mime_type,
        )
    if uri.startswith(("http://", "https://")):
        with urlopen(uri, timeout=settings.provider_timeout_sec) as response:
            body = response.read()
            mime_type = (
                response.headers.get("Content-Type")
                or mimetypes.guess_type(urlparse(uri).path)[0]
                or "image/png"
            )
        return _LTX23ImageInput(
            image_name=Path(urlparse(uri).path).name or f"{request.task_id}.png",
            image_bytes=body,
            image_mime=mime_type,
        )
    raise ValueError(f"Unsupported image reference URI for LTX-2.3: {uri[:32]}")


def _multipart_body(
    fields: dict[str, object],
    *,
    file_field: str,
    filename: str,
    file_bytes: bytes,
    file_mime: str,
) -> tuple[bytes, str]:
    boundary = f"----verbascene-ltx23-{int(time.time() * 1000)}"
    parts: list[bytes] = []
    for key, value in fields.items():
        parts.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode(
                    "utf-8"
                ),
                str(value).encode("utf-8"),
                b"\r\n",
            ]
        )
    parts.extend(
        [
            f"--{boundary}\r\n".encode("utf-8"),
            (
                f'Content-Disposition: form-data; name="{file_field}"; '
                f'filename="{filename}"\r\n'
                f"Content-Type: {file_mime}\r\n\r\n"
            ).encode("utf-8"),
            file_bytes,
            b"\r\n",
            f"--{boundary}--\r\n".encode("utf-8"),
        ]
    )
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _urlopen_json(request: Request, *, timeout: float) -> dict:
    with urlopen(request, timeout=timeout) as response:
        body = response.read()
    if not body:
        return {}
    parsed = json.loads(body.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("LTX-2.3 API returned a non-object response")
    return parsed


def _job_id(payload: dict) -> str:
    data = _data(payload)
    value = data.get("job_id") or data.get("id")
    if not value:
        raise ValueError("LTX-2.3 response did not include job_id")
    return str(value)


def _data(payload: dict) -> dict:
    value = payload.get("data")
    return value if isinstance(value, dict) else payload


def _normalized_status(payload: dict) -> str:
    value = str(_status_value(payload, "status", "state") or "").lower()
    if value in {"success", "completed", "complete", "done", "finished"}:
        return "succeeded"
    if value == "error":
        return "failed"
    if value in {"canceled", "aborted"}:
        return "cancelled"
    return value


def _provider_status(payload: dict) -> ProviderStatus:
    return {
        "queued": ProviderStatus.QUEUED,
        "pending": ProviderStatus.QUEUED,
        "running": ProviderStatus.RUNNING,
        "processing": ProviderStatus.RUNNING,
        "succeeded": ProviderStatus.SUCCEEDED,
        "failed": ProviderStatus.FAILED,
        "cancelled": ProviderStatus.CANCELLED,
    }.get(_normalized_status(payload), ProviderStatus.RUNNING)


def _status_value(payload: dict, *keys: str) -> object | None:
    data = _data(payload)
    for key in keys:
        value = data.get(key)
        if value is not None:
            return value
    return None


def _job_error_message(payload: dict) -> str:
    value = _status_value(
        payload,
        "error",
        "error_message",
        "message",
        "detail",
    )
    if isinstance(value, dict):
        return str(
            value.get("message")
            or value.get("detail")
            or value.get("code")
            or value
        )
    return str(value or "LTX-2.3 job failed")


def _duration_from_params(request: ProviderRequest) -> float:
    params = _generation_params(request)
    return params.frames / params.fps


def _result_dimensions(
    request: ProviderRequest,
    final_status: dict,
) -> tuple[int, int]:
    width = _status_value(final_status, "width")
    height = _status_value(final_status, "height")
    try:
        if width is not None and height is not None:
            parsed = int(width), int(height)
            if parsed[0] > 0 and parsed[1] > 0:
                return parsed
    except (TypeError, ValueError):
        pass
    return _video_dimensions(request)


def _video_mime_type(content_type: str) -> str:
    value = content_type.split(";", 1)[0].strip().lower()
    return value if value.startswith("video/") else "video/mp4"


def _temporary_video_result(
    job_id: str,
    video_bytes: bytes,
    mime_type: str,
) -> str:
    suffix = mimetypes.guess_extension(mime_type) or ".mp4"
    with tempfile.NamedTemporaryFile(
        prefix=f"verbascene-ltx23-{job_id}-",
        suffix=suffix,
        delete=False,
    ) as output:
        output.write(video_bytes)
        return output.name


def _ltx23_model() -> str:
    if settings.video_provider == "ltx23_api" and settings.video_model not in {
        "",
        "mock-video",
    }:
        return settings.video_model
    return "LTX-2.3"


def _failed_response(
    provider_task_id: str,
    code: str,
    message: str,
    *,
    raw_response: dict,
    retryable: bool,
    submission_state: SubmissionState = SubmissionState.NOT_SUBMITTED,
) -> ProviderResponse:
    return ProviderResponse(
        status=ProviderStatus.FAILED,
        provider_task_id=provider_task_id,
        execution_mode=ProviderExecutionMode.ASYNC,
        raw_response=raw_response,
        error=ProviderError(
            error_code=code,
            error_message=message,
            is_retryable=retryable,
            submission_state=submission_state,
            raw_error=raw_response,
        ),
    )


def _exception_details(exc: Exception) -> tuple[str, dict]:
    if isinstance(exc, HTTPError):
        raw_text = exc.read().decode("utf-8", errors="replace")
        try:
            raw = json.loads(raw_text)
        except json.JSONDecodeError:
            raw = {"raw_text": raw_text, "status_code": exc.code}
        detail = raw.get("detail") if isinstance(raw, dict) else None
        return str(detail or raw or exc), raw if isinstance(raw, dict) else {}
    return str(exc), {}


def build_ltx23_providers() -> list[ProviderAdapter]:
    return [LTX23ApiProvider()]
