import json
import mimetypes
import tempfile
from decimal import Decimal
from urllib.error import HTTPError, URLError
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


class Wan2T2VApiProvider(ProviderAdapter):
    name = "wan2_t2v_api"
    type = ProviderType.VIDEO
    capabilities = ["text_to_video", "async_api", "poll", "polling", "cancel", "wan2_t2v"]

    def __init__(self) -> None:
        self.model = _wan_model()
        self.base_url = settings.wan_t2v_api_base_url.rstrip("/")
        self.api_key = settings.wan_t2v_api_key or settings.video_api_key

    def validate_config(self) -> None:
        if not self.base_url:
            raise ValueError("WAN_T2V_API_BASE_URL is required for Wan2 T2V API provider")
        if not self.api_key:
            raise ValueError("WAN_T2V_API_KEY or VIDEO_API_KEY is required for Wan2 T2V API provider")

    def submit(self, request: ProviderRequest) -> ProviderResponse:
        provider_task_id = str(
            uuid5(NAMESPACE_URL, f"{request.project_id}:{request.task_id}:wan2-t2v")
        )
        submission_state = SubmissionState.NOT_SUBMITTED
        try:
            self.validate_config()
            submission_state = SubmissionState.UNKNOWN
            job = self._submit_job(request, client_job_id=provider_task_id)
            job_id = _job_id(job)
            provider_task_id = job_id
            submission_state = SubmissionState.ACCEPTED
        except HTTPError as exc:
            raw_error = _http_error_payload(exc)
            return _failed_response(
                provider_task_id,
                "wan2_t2v_api_failed",
                _http_error_message(exc, raw_error),
                raw_response=raw_error,
                retryable=exc.code >= 500 or exc.code == 429,
                submission_state=SubmissionState.NOT_SUBMITTED,
            )
        except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            return _failed_response(
                provider_task_id,
                "wan2_t2v_api_failed",
                str(exc),
                raw_response={},
                retryable=True,
                submission_state=submission_state,
            )
        return ProviderResponse(
            status=_provider_status(job),
            provider_task_id=provider_task_id,
            execution_mode=ProviderExecutionMode.ASYNC,
            poll_after_sec=settings.wan_t2v_api_poll_interval_sec,
            raw_response={
                "model": self.model,
                "wan_job_id": job_id,
                "job": job,
            },
            usage=ProviderUsage(cost=Decimal("0"), unit="USD"),
        )

    def poll(self, provider_task_id: str) -> ProviderResponse:
        self.validate_config()
        raw = _data(self._get_job(provider_task_id))
        status = _provider_status(raw)
        error = None
        if status in {ProviderStatus.FAILED, ProviderStatus.CANCELLED}:
            error = ProviderError(
                error_code="wan2_t2v_job_failed",
                error_message=str(raw.get("error") or "Wan2 T2V job failed"),
                submission_state=SubmissionState.ACCEPTED,
                raw_error=raw,
            )
        return ProviderResponse(
            status=status,
            provider_task_id=provider_task_id,
            execution_mode=ProviderExecutionMode.ASYNC,
            poll_after_sec=(
                settings.wan_t2v_api_poll_interval_sec
                if status in {ProviderStatus.QUEUED, ProviderStatus.RUNNING}
                else None
            ),
            raw_response=raw,
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
        if request is None:
            raise ValueError("Wan2 T2V fetch_result requires the original request")
        polled = self.poll(provider_task_id)
        if polled.status != ProviderStatus.SUCCEEDED:
            return polled
        video_bytes, content_type = self._download_result(provider_task_id)
        mime_type = content_type.split(";", 1)[0] or "video/mp4"
        uri = _temporary_video_result(provider_task_id, video_bytes, mime_type)
        frame_num = _frame_num(request)
        fps = max(int(request.params.get("fps", settings.wan_i2v_fps)), 1)
        width, height = _result_dimensions(request)
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
                    duration_sec=Decimal(str(frame_num / fps)).quantize(Decimal("0.001")),
                    metadata={
                        "provider_task_id": provider_task_id,
                        "wan_job_id": provider_task_id,
                        "generation_mode": "text_to_video",
                        "temporary_file": True,
                    },
                )
            ],
            raw_response=polled.raw_response,
            usage=ProviderUsage(cost=Decimal("0"), unit="USD"),
        )

    def cancel(self, provider_task_id: str) -> None:
        self.validate_config()
        with urlopen(
            Request(
                f"{self.base_url}/v1/t2v/jobs/{provider_task_id}",
                headers=self._auth_headers(),
                method="DELETE",
            ),
            timeout=settings.wan_t2v_api_timeout_sec,
        ):
            return None

    def _submit_job(self, request: ProviderRequest, *, client_job_id: str) -> dict:
        payload: dict[str, object] = {
            "client_job_id": client_job_id,
            "prompt": request.prompt,
            "size": _size(request),
            "frame_num": _frame_num(request),
            "sample_steps": _sample_steps(request),
        }
        if request.negative_prompt:
            payload["negative_prompt"] = request.negative_prompt
        if request.params.get("guidance") is not None:
            payload["guidance"] = float(request.params["guidance"])
        if request.params.get("seed") is not None:
            payload["seed"] = int(request.params["seed"])

        http_request = Request(
            f"{self.base_url}/v1/t2v/jobs",
            data=json.dumps(payload).encode("utf-8"),
            headers={**self._auth_headers(), "Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(http_request, timeout=settings.wan_t2v_api_timeout_sec) as response:
            return json.loads(response.read().decode("utf-8"))

    def _get_job(self, job_id: str) -> dict:
        http_request = Request(
            f"{self.base_url}/v1/t2v/jobs/{job_id}",
            headers=self._auth_headers(),
            method="GET",
        )
        with urlopen(http_request, timeout=settings.wan_t2v_api_timeout_sec) as response:
            return json.loads(response.read().decode("utf-8"))

    def _download_result(self, job_id: str) -> tuple[bytes, str]:
        http_request = Request(
            f"{self.base_url}/v1/t2v/jobs/{job_id}/result",
            headers=self._auth_headers(),
            method="GET",
        )
        with urlopen(http_request, timeout=settings.provider_timeout_sec) as response:
            return response.read(), response.headers.get("Content-Type", "video/mp4")

    def _auth_headers(self) -> dict[str, str]:
        return {"X-API-Key": self.api_key or ""}


def _job_id(payload: dict) -> str:
    data = _data(payload)
    job_id = data.get("id") or data.get("job_id")
    if not job_id:
        raise ValueError("Wan2 T2V response did not include job id")
    return str(job_id)


def _data(payload: dict) -> dict:
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


def _provider_status(payload: dict) -> ProviderStatus:
    value = str(_data(payload).get("status") or "").lower()
    if value in {"queued", "pending", "created"}:
        return ProviderStatus.QUEUED
    if value in {"succeeded", "success", "completed", "done"}:
        return ProviderStatus.SUCCEEDED
    if value in {"failed", "error"}:
        return ProviderStatus.FAILED
    if value in {"cancelled", "canceled", "aborted"}:
        return ProviderStatus.CANCELLED
    return ProviderStatus.RUNNING


def _frame_num(request: ProviderRequest) -> int:
    return int(
        request.params.get(
            "frame_num",
            request.params.get("frames", settings.wan_i2v_frames),
        )
    )


def _sample_steps(request: ProviderRequest) -> int:
    return int(
        request.params.get(
            "sample_steps",
            request.params.get("steps", settings.wan_i2v_steps),
        )
    )


def _size(request: ProviderRequest) -> str:
    return str(request.params.get("size") or settings.wan_t2v_default_size).replace("x", "*")


def _result_dimensions(request: ProviderRequest) -> tuple[int, int]:
    size = _size(request)
    if "*" in size:
        width, height = size.split("*", 1)
        try:
            return int(width), int(height)
        except ValueError:
            pass
    return 832, 480


def _temporary_video_result(
    job_id: str,
    video_bytes: bytes,
    mime_type: str,
) -> str:
    suffix = mimetypes.guess_extension(mime_type) or ".mp4"
    with tempfile.NamedTemporaryFile(
        prefix=f"verbascene-wan-t2v-{job_id}-",
        suffix=suffix,
        delete=False,
    ) as output:
        output.write(video_bytes)
        return output.name


def _wan_model() -> str:
    if settings.video_provider == "wan2_t2v_api" and settings.video_model not in {"", "mock-video"}:
        return settings.video_model
    return "Wan-AI/Wan2.2-T2V-A14B"


def _http_error_payload(exc: HTTPError) -> dict:
    raw_text = exc.read().decode("utf-8", errors="replace")
    try:
        payload = json.loads(raw_text)
        return payload if isinstance(payload, dict) else {"raw_text": raw_text}
    except json.JSONDecodeError:
        return {"raw_text": raw_text, "status_code": exc.code}


def _http_error_message(exc: HTTPError, payload: dict) -> str:
    detail = payload.get("detail")
    if detail:
        return str(detail)
    error = payload.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or exc)
    if error:
        return str(error)
    return str(payload.get("raw_text") or exc)


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


def build_wan_t2v_providers() -> list[ProviderAdapter]:
    return [Wan2T2VApiProvider()]
