import base64
import json
import mimetypes
import tempfile
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote, urlparse
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
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


class Wan2I2VApiProvider(ProviderAdapter):
    name = "wan2_i2v_api"
    type = ProviderType.VIDEO
    capabilities = ["image_to_video", "async_api", "poll", "polling", "cancel", "wan2_i2v"]
    reference_images = True
    max_duration_sec = 5
    supported_resolutions = ["1280x720"]

    def __init__(self) -> None:
        self.model = _wan_model()
        self.base_url = settings.wan_i2v_api_base_url.rstrip("/")
        self.api_key = settings.wan_i2v_api_key or settings.video_api_key
        self.protocol = _api_protocol()

    def validate_config(self) -> None:
        if not self.base_url:
            raise ValueError("WAN_I2V_API_BASE_URL is required for Wan2 I2V API provider")
        if self.protocol in {"legacy", "official"} and not self.api_key:
            raise ValueError("WAN_I2V_API_KEY or VIDEO_API_KEY is required for Wan2 I2V API provider")

    def submit(self, request: ProviderRequest) -> ProviderResponse:
        provider_task_id = str(uuid5(NAMESPACE_URL, f"{request.project_id}:{request.task_id}:wan2-i2v"))
        submission_state = SubmissionState.NOT_SUBMITTED
        try:
            self.validate_config()
            image_input = _primary_image(request)
            # Submission helpers perform the network POST. Once entered, only
            # an explicit HTTP rejection proves the request was not accepted.
            submission_state = SubmissionState.UNKNOWN
            if self.protocol == "persistent":
                job = self._submit_persistent_job(request, client_job_id=provider_task_id, image_input=image_input)
                job_id = _job_id(job)
                provider_task_id = job_id
                submission_state = SubmissionState.ACCEPTED
                provider_status = ProviderStatus.SUCCEEDED
            else:
                job = self._submit_job(
                    request,
                    client_job_id=provider_task_id,
                    image_input=image_input,
                )
                job_id = _job_id(job)
                provider_task_id = job_id
                submission_state = SubmissionState.ACCEPTED
                provider_status = _provider_status(job)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            return _failed_response(
                provider_task_id,
                "wan2_i2v_api_failed",
                _error_message(exc),
                raw_response=_raw_http_error(exc),
                retryable=True,
                submission_state=(
                    SubmissionState.NOT_SUBMITTED
                    if isinstance(exc, HTTPError)
                    else submission_state
                ),
            )
        return ProviderResponse(
            status=provider_status,
            provider_task_id=provider_task_id,
            execution_mode=ProviderExecutionMode.ASYNC,
            poll_after_sec=(
                settings.wan_i2v_api_poll_interval_sec
                if provider_status in {ProviderStatus.QUEUED, ProviderStatus.RUNNING}
                else None
            ),
            raw_response={
                "model": self.model,
                "wan_job_id": job_id,
                "job": job,
                "protocol": self.protocol,
                "result_variant": settings.wan_i2v_api_result_variant,
            },
            usage=ProviderUsage(cost=Decimal("0"), unit="USD"),
        )

    def poll(self, provider_task_id: str) -> ProviderResponse:
        self.validate_config()
        if self.protocol == "persistent":
            raise NotImplementedError("Wan persistent protocol has no polling endpoint")
        status_payload = _data(self._get_job(provider_task_id))
        provider_status = _provider_status(status_payload)
        error = None
        if provider_status in {ProviderStatus.FAILED, ProviderStatus.CANCELLED, ProviderStatus.TIMEOUT}:
            error = ProviderError(
                error_code="wan2_i2v_job_failed",
                error_message=str(
                    status_payload.get("error")
                    or status_payload.get("message")
                    or "Wan2 I2V job failed"
                ),
                is_retryable=False,
                submission_state=SubmissionState.ACCEPTED,
                raw_error=status_payload,
            )
        return ProviderResponse(
            status=provider_status,
            provider_task_id=provider_task_id,
            execution_mode=ProviderExecutionMode.ASYNC,
            poll_after_sec=(
                settings.wan_i2v_api_poll_interval_sec
                if provider_status in {ProviderStatus.QUEUED, ProviderStatus.RUNNING}
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
        if request is None:
            raise ValueError("Wan2 fetch_result requires the original request snapshot")
        if self.protocol == "persistent":
            job = (
                provider_context.get("job")
                if isinstance(provider_context, dict) and isinstance(provider_context.get("job"), dict)
                else None
            )
            if job is None:
                raise ValueError("Wan persistent result context is missing")
            final_status = {"status": "succeeded", **job}
            video_bytes, content_type = self._download_persistent_result(job)
        else:
            polled = self.poll(provider_task_id)
            if polled.status != ProviderStatus.SUCCEEDED:
                return polled
            final_status = polled.raw_response
            video_bytes, content_type = self._download_result(provider_task_id)
        mime_type = content_type.split(";", 1)[0] or "video/mp4"
        uri = _temporary_video_result(provider_task_id, video_bytes, mime_type)
        duration = Decimal(
            str(
                final_status.get("duration_sec")
                or request.params.get("duration_sec")
                or _duration_from_params(request)
            )
        ).quantize(Decimal("0.001"))
        width, height = _result_dimensions(request, final_status)
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
                        "wan_job_id": provider_task_id,
                        "temporary_file": True,
                    },
                )
            ],
            raw_response=final_status,
            usage=ProviderUsage(cost=Decimal("0"), unit="USD"),
        )

    def cancel(self, provider_task_id: str) -> None:
        self.validate_config()
        if self.protocol == "persistent":
            raise NotImplementedError("Wan persistent protocol does not support cancellation")
        path = f"/v1/jobs/{provider_task_id}" if self.protocol == "local" else f"/v1/i2v/jobs/{provider_task_id}"
        with urlopen(
            Request(
                f"{self.base_url}{path}",
                headers=self._auth_headers(),
                method="DELETE",
            ),
            timeout=settings.wan_i2v_api_timeout_sec,
        ):
            return None

    def _submit_job(
        self,
        request: ProviderRequest,
        *,
        client_job_id: str,
        image_input: "_WanImageInput",
    ) -> dict:
        if self.protocol == "local":
            return self._submit_local_job(request, image_input=image_input)
        if self.protocol == "official":
            return self._submit_official_job(
                request,
                client_job_id=client_job_id,
                image_input=image_input,
            )
        return self._submit_legacy_job(request, client_job_id=client_job_id, image_input=image_input)

    def _submit_local_job(
        self,
        request: ProviderRequest,
        *,
        image_input: "_WanImageInput",
    ) -> dict:
        upload_input = _ensure_upload_image(request, image_input)
        fields = {
            "prompt": request.prompt,
            "size": _local_size(request),
            "mode": request.params.get("mode", settings.wan_i2v_api_mode),
            "gpu_devices": request.params.get("gpu_devices", settings.wan_i2v_api_gpu_devices),
        }
        if request.params.get("frames") is not None:
            fields["frame_num"] = request.params["frames"]
        if request.params.get("frame_num") is not None:
            fields["frame_num"] = request.params["frame_num"]
        if request.params.get("steps") is not None:
            fields["sample_steps"] = request.params["steps"]
        if request.params.get("sample_steps") is not None:
            fields["sample_steps"] = request.params["sample_steps"]
        seed = _video_seed(request)
        if seed is not None:
            fields["seed"] = seed

        body, content_type = _multipart_body(
            fields,
            "image",
            upload_input.image_name or f"{request.task_id}.png",
            upload_input.image_bytes or b"",
            upload_input.image_mime or "image/png",
        )
        http_request = Request(
            f"{self.base_url}/v1/i2v",
            data=body,
            headers={
                **self._auth_headers(),
                "Content-Type": content_type,
            },
            method="POST",
        )
        with urlopen(http_request, timeout=settings.wan_i2v_api_timeout_sec) as response:
            return json.loads(response.read().decode("utf-8"))

    def _submit_persistent_job(
        self,
        request: ProviderRequest,
        *,
        client_job_id: str,
        image_input: "_WanImageInput",
    ) -> dict:
        upload_input = _ensure_upload_image(request, image_input)
        payload: dict[str, object] = {
            "job_id": client_job_id,
            "prompt": request.prompt,
            "image_base64": base64.b64encode(upload_input.image_bytes or b"").decode("ascii"),
            "size": _local_size(request),
            "frame_num": request.params.get("frame_num", request.params.get("frames", settings.wan_i2v_frames)),
            "sample_steps": request.params.get("sample_steps", request.params.get("steps", settings.wan_i2v_steps)),
        }
        seed = _video_seed(request)
        if seed is not None:
            payload["seed"] = seed
        http_request = Request(
            f"{self.base_url}/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={**self._auth_headers(), "Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(http_request, timeout=settings.wan_i2v_api_job_timeout_sec) as response:
            return json.loads(response.read().decode("utf-8"))

    def _submit_legacy_job(
        self,
        request: ProviderRequest,
        *,
        client_job_id: str,
        image_input: "_WanImageInput",
    ) -> dict:
        seed = _video_seed(request)
        fields = {
            "prompt": request.prompt,
            "frames": request.params.get("frames", settings.wan_i2v_frames),
            "steps": request.params.get("steps", settings.wan_i2v_steps),
            "guidance": request.params.get("guidance", settings.wan_i2v_guidance),
            "seed": seed if seed is not None else 0,
            "fps": request.params.get("fps", settings.wan_i2v_fps),
            "max_area": request.params.get("max_area", settings.wan_i2v_max_area),
            "upscale_1080p": _form_bool(request.params.get("upscale_1080p", settings.wan_i2v_upscale_1080p)),
            "client_job_id": client_job_id,
        }
        if request.negative_prompt:
            fields["negative_prompt"] = request.negative_prompt
        if image_input.image_url:
            fields["image_url"] = image_input.image_url
            body, content_type = _multipart_body(fields)
        else:
            body, content_type = _multipart_body(
                fields,
                "image",
                image_input.image_name or f"{request.task_id}.png",
                image_input.image_bytes or b"",
                image_input.image_mime or "image/png",
            )
        http_request = Request(
            f"{self.base_url}/v1/i2v/jobs",
            data=body,
            headers={
                **self._auth_headers(),
                "Content-Type": content_type,
            },
            method="POST",
        )
        return _urlopen_json_with_retries(
            http_request,
            timeout=settings.wan_i2v_api_timeout_sec,
            attempts=3,
        )

    def _submit_official_job(
        self,
        request: ProviderRequest,
        *,
        client_job_id: str,
        image_input: "_WanImageInput",
    ) -> dict:
        upload_input = _ensure_upload_image(request, image_input)
        frames, steps, guidance = _official_generation_params(request)
        fields = {
            "prompt": request.prompt,
            "frames": frames,
            "steps": steps,
            "guidance": guidance,
            "size": _local_size(request),
            "client_job_id": client_job_id,
        }
        if request.negative_prompt:
            fields["negative_prompt"] = request.negative_prompt
        seed = _video_seed(request)
        if seed is not None:
            fields["seed"] = seed
        if request.params.get("fps") is not None:
            fields["fps"] = request.params["fps"]
        body, content_type = _multipart_body(
            fields,
            "image",
            upload_input.image_name or f"{request.task_id}.png",
            upload_input.image_bytes or b"",
            upload_input.image_mime or "image/png",
        )
        http_request = Request(
            f"{self.base_url}/v1/i2v/jobs",
            data=body,
            headers={
                **self._auth_headers(),
                "Content-Type": content_type,
            },
            method="POST",
        )
        with urlopen(http_request, timeout=settings.wan_i2v_api_timeout_sec) as response:
            return json.loads(response.read().decode("utf-8"))

    def _poll_job(self, job_id: str) -> dict:
        deadline = time.monotonic() + settings.wan_i2v_api_job_timeout_sec
        while True:
            status_payload = self._get_job(job_id)
            data = _data(status_payload)
            status_value = str(data.get("status") or "").lower()
            if status_value in {"succeeded", "success", "completed", "done", "failed", "error"}:
                if status_value in {"success", "completed", "done"}:
                    data["status"] = "succeeded"
                if status_value == "error":
                    data["status"] = "failed"
                return data
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Wan2 I2V job timed out: {job_id}")
            time.sleep(settings.wan_i2v_api_poll_interval_sec)

    def _get_job(self, job_id: str) -> dict:
        path = f"/v1/jobs/{job_id}" if self.protocol == "local" else f"/v1/i2v/jobs/{job_id}"
        http_request = Request(
            f"{self.base_url}{path}",
            headers=self._auth_headers(),
            method="GET",
        )
        with urlopen(http_request, timeout=settings.wan_i2v_api_timeout_sec) as response:
            return json.loads(response.read().decode("utf-8"))

    def _download_result(self, job_id: str) -> tuple[bytes, str]:
        if self.protocol == "local":
            http_request = Request(
                f"{self.base_url}/v1/jobs/{job_id}/video",
                headers=self._auth_headers(),
                method="GET",
            )
            with urlopen(http_request, timeout=settings.provider_timeout_sec) as response:
                return response.read(), response.headers.get("Content-Type", "video/mp4")

        query = urlencode({"variant": settings.wan_i2v_api_result_variant})
        http_request = Request(
            f"{self.base_url}/v1/i2v/jobs/{job_id}/result?{query}",
            headers=self._auth_headers(),
            method="GET",
        )
        with urlopen(http_request, timeout=settings.provider_timeout_sec) as response:
            return response.read(), response.headers.get("Content-Type", "video/mp4")

    def _download_persistent_result(self, job: dict) -> tuple[bytes, str]:
        save_file = str(job.get("save_file") or "")
        if not save_file:
            raise ValueError("Wan persistent response did not include save_file")
        filename = Path(save_file).name
        http_request = Request(
            f"{self.base_url}/outputs/{quote(filename)}",
            headers=self._auth_headers(),
            method="GET",
        )
        with urlopen(http_request, timeout=settings.provider_timeout_sec) as response:
            return response.read(), response.headers.get("Content-Type", "video/mp4")

    def _auth_headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        if self.protocol == "official":
            return {"X-API-Key": self.api_key}
        return {"Authorization": f"Bearer {self.api_key}"}


@dataclass(frozen=True)
class _WanImageInput:
    image_url: str | None = None
    image_name: str | None = None
    image_bytes: bytes | None = None
    image_mime: str | None = None


def _primary_image(request: ProviderRequest) -> _WanImageInput:
    if not request.references:
        raise ValueError("Wan2 I2V requires an input image reference")
    uri = request.references[0]
    if uri.startswith("data:"):
        if _image_transport() == "url":
            raise ValueError("WAN_I2V_IMAGE_TRANSPORT=url requires an HTTP image reference")
        header, encoded = uri.split(",", 1)
        mime_type = header.split(":", 1)[1].split(";", 1)[0] or "image/png"
        return _WanImageInput(
            image_name=f"{request.task_id}.png",
            image_bytes=base64.b64decode(encoded),
            image_mime=mime_type,
        )
    if uri.startswith(("http://", "https://")):
        remote_url = _remote_image_url(uri)
        if _api_protocol() != "local" and _should_send_image_url(remote_url):
            return _WanImageInput(image_url=remote_url)
        if _api_protocol() != "local" and _image_transport() == "url":
            raise ValueError(
                "WAN_I2V_IMAGE_TRANSPORT=url requires a remote-accessible image URL; "
                "set WAN_I2V_REMOTE_STORAGE_BASE_URL when references use localhost storage URLs"
            )
        with urlopen(uri, timeout=settings.provider_timeout_sec) as response:
            body = response.read()
            mime_type = response.headers.get("Content-Type") or mimetypes.guess_type(uri)[0] or "image/png"
            return _WanImageInput(
                image_name=uri.rsplit("/", 1)[-1] or f"{request.task_id}.png",
                image_bytes=body,
                image_mime=mime_type,
            )
    raise ValueError(f"Unsupported image reference URI for Wan2 I2V: {uri[:32]}")


def _ensure_upload_image(request: ProviderRequest, image_input: _WanImageInput) -> _WanImageInput:
    if image_input.image_bytes is not None:
        return image_input
    if not image_input.image_url:
        raise ValueError("Wan2 local API requires uploaded image bytes")
    with urlopen(image_input.image_url, timeout=settings.provider_timeout_sec) as response:
        body = response.read()
        mime_type = response.headers.get("Content-Type") or mimetypes.guess_type(image_input.image_url)[0] or "image/png"
        return _WanImageInput(
            image_name=image_input.image_url.rsplit("/", 1)[-1] or f"{request.task_id}.png",
            image_bytes=body,
            image_mime=mime_type,
        )


def _multipart_body(
    fields: dict,
    file_field: str | None = None,
    filename: str | None = None,
    file_bytes: bytes | None = None,
    file_mime: str | None = None,
) -> tuple[bytes, str]:
    boundary = f"----verbascene-wan-i2v-{int(time.time() * 1000)}"
    parts: list[bytes] = []
    for key, value in fields.items():
        parts.append(f"--{boundary}\r\n".encode("utf-8"))
        parts.append(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"))
        parts.append(str(value).encode("utf-8"))
        parts.append(b"\r\n")
    if file_field and filename and file_bytes is not None:
        parts.append(f"--{boundary}\r\n".encode("utf-8"))
        parts.append(
            (
                f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
                f"Content-Type: {file_mime or 'application/octet-stream'}\r\n\r\n"
            ).encode("utf-8")
        )
        parts.append(file_bytes)
        parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _urlopen_json_with_retries(request: Request, *, timeout: float, attempts: int) -> dict:
    last_error: Exception | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            with urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt >= attempts or isinstance(exc, HTTPError):
                raise
            time.sleep(min(2.0, 0.5 * attempt))
    if last_error:
        raise last_error
    raise TimeoutError("Wan2 I2V request did not return a response")


def _image_transport() -> str:
    value = settings.wan_i2v_image_transport.lower().strip()
    if value not in {"auto", "url", "upload"}:
        raise ValueError("WAN_I2V_IMAGE_TRANSPORT must be one of: auto, url, upload")
    return value


def _remote_image_url(uri: str) -> str:
    local_base = settings.public_storage_base_url.rstrip("/")
    remote_base = (settings.wan_i2v_remote_storage_base_url or "").rstrip("/")
    if remote_base and uri.startswith(local_base + "/"):
        return remote_base + uri[len(local_base) :]
    return uri


def _should_send_image_url(uri: str) -> bool:
    transport = _image_transport()
    if transport == "upload":
        return False
    if transport == "url":
        return True
    return not _is_loopback_url(uri)


def _is_loopback_url(uri: str) -> bool:
    host = (urlparse(uri).hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"}


def _job_id(payload: dict) -> str:
    data = _data(payload)
    job_id = data.get("id") or data.get("job_id")
    if not job_id:
        raise ValueError("Wan2 I2V response did not include job id")
    return str(job_id)


def _data(payload: dict) -> dict:
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


def _provider_status(payload: dict) -> ProviderStatus:
    value = str(_data(payload).get("status") or "").strip().lower()
    if value in {"queued", "pending", "created"}:
        return ProviderStatus.QUEUED
    if value in {"succeeded", "success", "completed", "complete", "done", "finished"}:
        return ProviderStatus.SUCCEEDED
    if value in {"failed", "error"}:
        return ProviderStatus.FAILED
    if value in {"cancelled", "canceled", "aborted"}:
        return ProviderStatus.CANCELLED
    return ProviderStatus.RUNNING


def _duration_from_params(request: ProviderRequest) -> float:
    frames = int(request.params.get("frames", settings.wan_i2v_frames))
    fps = int(request.params.get("fps", settings.wan_i2v_fps))
    return frames / max(fps, 1)


def _local_size(request: ProviderRequest) -> str:
    size = str(request.params.get("size") or settings.wan_i2v_size or "1280*720")
    return size.replace("x", "*")


def _video_seed(request: ProviderRequest) -> int | None:
    value = request.params.get("seed")
    if value is None:
        value = settings.wan_i2v_seed
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return int(value)


def _official_generation_params(request: ProviderRequest) -> tuple[int, int, float]:
    frames = int(request.params.get("frames", settings.wan_i2v_frames))
    steps = int(request.params.get("steps", settings.wan_i2v_steps))
    guidance = float(request.params.get("guidance", settings.wan_i2v_guidance))
    if frames < 1 or (frames - 1) % 4 != 0:
        raise ValueError("Wan official API requires frames=4n+1 (for example 17, 49, or 81)")
    if steps != 4:
        raise ValueError("Wan official API at 18083 requires steps=4")
    if guidance != 1.0:
        raise ValueError("Wan official API at 18083 requires guidance=1")
    return frames, steps, guidance


def _result_dimensions(
    request: ProviderRequest,
    final_status: dict | None = None,
) -> tuple[int, int]:
    if final_status:
        try:
            width = int(final_status.get("width") or 0)
            height = int(final_status.get("height") or 0)
            if width > 0 and height > 0:
                return width, height
        except (TypeError, ValueError):
            pass
    if _api_protocol() != "local" and settings.wan_i2v_api_result_variant == "1080p":
        return 1920, 1080
    size = _local_size(request)
    if "*" in size:
        width, height = size.split("*", 1)
        try:
            return int(width), int(height)
        except ValueError:
            pass
    return 1280, 720


def _api_protocol() -> str:
    value = settings.wan_i2v_api_protocol.lower().strip()
    if value not in {"local", "legacy", "persistent", "official"}:
        raise ValueError("WAN_I2V_API_PROTOCOL must be one of: local, legacy, persistent, official")
    return value


def _temporary_video_result(job_id: str, video_bytes: bytes, mime_type: str) -> str:
    suffix = mimetypes.guess_extension(mime_type) or ".mp4"
    with tempfile.NamedTemporaryFile(
        prefix=f"verbascene-wan-{job_id}-",
        suffix=suffix,
        delete=False,
    ) as output:
        output.write(video_bytes)
        return output.name


def _form_bool(value: object) -> str:
    if isinstance(value, str):
        return "true" if value.lower() in {"1", "true", "yes", "on"} else "false"
    return "true" if bool(value) else "false"


def _wan_model() -> str:
    if settings.video_provider == "wan2_i2v_api" and settings.video_model not in {"", "mock-video"}:
        return settings.video_model
    return "wan2.2-i2v-a14b"


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


def _raw_http_error(exc: Exception) -> dict:
    if isinstance(exc, HTTPError):
        raw_text = exc.read().decode("utf-8", errors="replace")
        try:
            return json.loads(raw_text)
        except json.JSONDecodeError:
            return {"raw_text": raw_text, "status_code": exc.code}
    return {}


def _error_message(exc: Exception) -> str:
    raw = _raw_http_error(exc)
    if raw:
        error = raw.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error.get("code") or exc)
        if error:
            return str(error)
        if raw.get("detail"):
            return str(raw["detail"])
        if raw.get("raw_text"):
            return str(raw["raw_text"])
    return str(exc)


def build_wan_i2v_providers() -> list[ProviderAdapter]:
    return [Wan2I2VApiProvider()]
