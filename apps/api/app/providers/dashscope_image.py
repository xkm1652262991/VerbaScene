from __future__ import annotations

import base64
import json
import time
from decimal import Decimal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import NAMESPACE_URL, uuid5

from app.core.config import settings
from app.providers.base import ProviderAdapter
from app.providers.image_reference_payload import read_reference_image
from app.providers.types import (
    ProviderAsset,
    ProviderError,
    ProviderRequest,
    ProviderResponse,
    ProviderStatus,
    ProviderType,
    ProviderUsage,
)

QWEN_MAX_REFERENCE_IMAGE_BYTES = 10 * 1024 * 1024


class DashScopeImageProvider(ProviderAdapter):
    """Alibaba Cloud Model Studio (DashScope) text-to-image adapter.

    DashScope image models do not use the OpenAI Images contract. Wan image
    models use an asynchronous task API, while Qwen-Image 2.0 and 3.0 use the
    synchronous multimodal-generation API. Result URLs are always downloaded
    before returning so project assets do not depend on short-lived links.
    """

    name = "dashscope_image"
    type = ProviderType.IMAGE
    capabilities = ["text_to_image"]

    def __init__(
        self,
        *,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        default_params: dict | None = None,
    ) -> None:
        self.model = model or settings.dashscope_image_model or settings.image_model
        self.api_key = (
            api_key
            if api_key is not None
            else settings.dashscope_image_api_key or settings.dashscope_api_key or settings.image_api_key
        )
        self.base_url = (base_url or settings.dashscope_image_base_url).rstrip("/")
        self.default_params = dict(default_params or {})
        self.capabilities = (
            ["text_to_image", "reference_to_image", "reference_payload", "sync_api"]
            if _uses_qwen_sync_protocol(self.model)
            else ["text_to_image", "async_api", "poll", "polling"]
        )

    def validate_config(self) -> None:
        if not self.base_url:
            raise ValueError("DASHSCOPE_IMAGE_BASE_URL is required for dashscope_image provider")
        if not self.api_key:
            raise ValueError("DASHSCOPE_API_KEY or DASHSCOPE_IMAGE_API_KEY is required for dashscope_image provider")

    def submit(self, request: ProviderRequest) -> ProviderResponse:
        provider_task_id = str(
            uuid5(NAMESPACE_URL, f"{request.project_id}:{request.task_id}:dashscope-image")
        )
        references_sent = 0
        references_dropped = 0
        try:
            self.validate_config()
            model = request.model or self.model
            if request.references and not _uses_qwen_sync_protocol(model):
                raise ValueError(
                    f"DashScope image model {model} does not support reference images through this adapter"
                )
            if _uses_qwen_sync_protocol(model):
                references_sent = min(len(request.references), 3)
                references_dropped = max(0, len(request.references) - references_sent)
            submitted = self._submit_task(request)
            if _uses_qwen_sync_protocol(model):
                completed = submitted
                request_id = _request_id(completed)
                if request_id:
                    provider_task_id = f"dashscope:{request_id}"
            else:
                task_id = _task_id(submitted)
                provider_task_id = f"dashscope:{task_id}"
                completed = self._poll_task(task_id)
                task_status = _task_status(completed)
                if task_status != "succeeded":
                    return _failed_response(
                        provider_task_id,
                        "dashscope_image_job_failed",
                        _error_message(completed, f"DashScope image task ended with status {task_status}"),
                        {"submitted": submitted, "completed": completed},
                        False,
                    )
            image_url = _image_url(completed)
            if not image_url:
                return _failed_response(
                    provider_task_id,
                    "dashscope_image_missing_asset",
                    "DashScope image response did not include an output image URL",
                    {"submitted": submitted, "completed": completed},
                    False,
                )
            image_bytes, mime_type = self._download_image(image_url)
            width, height = _response_dimensions(completed, request, self.default_params, model=model)
        except HTTPError as exc:
            raw_error = _read_http_error(exc)
            return _failed_response(
                provider_task_id,
                "dashscope_image_http_error",
                _error_message(raw_error, str(exc)),
                raw_error,
                exc.code in {408, 409, 429, 500, 502, 503, 504},
            )
        except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            return _failed_response(
                provider_task_id,
                "dashscope_image_failed",
                str(exc),
                {},
                True,
            )

        return ProviderResponse(
            status=ProviderStatus.SUCCEEDED,
            provider_task_id=provider_task_id,
            assets=[
                ProviderAsset(
                    asset_type="image",
                    uri=f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}",
                    mime_type=mime_type,
                    width=width,
                    height=height,
                    metadata={
                        "provider_task_id": provider_task_id,
                        "reference_payload_count": references_sent,
                        "reference_payload_dropped": references_dropped,
                    },
                )
            ],
            raw_response={
                "model": request.model or self.model,
                "submitted": submitted,
                "completed": completed,
                "result_materialized": True,
                "reference_payload_count": references_sent,
                "reference_payload_dropped": references_dropped,
            },
            usage=ProviderUsage(cost=Decimal("0"), unit="CNY"),
        )

    def _submit_task(self, request: ProviderRequest) -> dict:
        model = request.model or self.model
        qwen_sync_protocol = _uses_qwen_sync_protocol(model)
        modern_protocol = _uses_modern_protocol(model) or qwen_sync_protocol
        parameters = {
            "size": _request_size(request, self.default_params, model=model),
            "n": int(request.params.get("n", self.default_params.get("n", 1))),
            "prompt_extend": _request_bool(
                request.params,
                self.default_params,
                "prompt_extend",
                settings.dashscope_image_prompt_extend,
            ),
            "watermark": _request_bool(
                request.params,
                self.default_params,
                "watermark",
                settings.dashscope_image_watermark,
            ),
        }
        seed = request.params.get("seed", self.default_params.get("seed"))
        if seed is not None and not (isinstance(seed, str) and not seed.strip()):
            normalized_seed = int(seed)
            if not 0 <= normalized_seed <= 2_147_483_647:
                raise ValueError("DashScope image seed must be between 0 and 2147483647")
            parameters["seed"] = normalized_seed
        negative_prompt = request.negative_prompt or request.params.get("negative_prompt")
        if modern_protocol:
            parameters["negative_prompt"] = str(negative_prompt or "")
            content = (
                [{"image": reference} for reference in _qwen_reference_data_uris(request.references)]
                if qwen_sync_protocol
                else []
            )
            content.append({"text": request.prompt})
            payload = {
                "model": model,
                "input": {
                    "messages": [
                        {
                            "role": "user",
                            "content": content,
                        }
                    ]
                },
                "parameters": parameters,
            }
            endpoint = (
                "/api/v1/services/aigc/multimodal-generation/generation"
                if qwen_sync_protocol
                else "/api/v1/services/aigc/image-generation/generation"
            )
        else:
            input_payload = {"prompt": request.prompt}
            if negative_prompt:
                input_payload["negative_prompt"] = str(negative_prompt)
            payload = {
                "model": model,
                "input": input_payload,
                "parameters": parameters,
            }
            endpoint = "/api/v1/services/aigc/text2image/image-synthesis"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if not qwen_sync_protocol:
            headers["X-DashScope-Async"] = "enable"
        http_request = Request(
            _api_url(self.base_url, endpoint),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urlopen(http_request, timeout=settings.provider_timeout_sec) as response:
            return json.loads(response.read().decode("utf-8"))

    def _poll_task(self, task_id: str) -> dict:
        deadline = time.monotonic() + settings.dashscope_image_job_timeout_sec
        while True:
            http_request = Request(
                _api_url(self.base_url, f"/api/v1/tasks/{task_id}"),
                headers={"Authorization": f"Bearer {self.api_key}"},
                method="GET",
            )
            with urlopen(http_request, timeout=settings.provider_timeout_sec) as response:
                payload = json.loads(response.read().decode("utf-8"))
            task_status = _task_status(payload)
            if task_status in {"succeeded", "failed", "cancelled"}:
                return payload
            if time.monotonic() >= deadline:
                raise TimeoutError(f"DashScope image task timed out: {task_id}")
            time.sleep(max(0, settings.dashscope_image_poll_interval_sec))

    def _download_image(self, image_url: str) -> tuple[bytes, str]:
        with urlopen(image_url, timeout=settings.provider_timeout_sec) as response:
            body = response.read()
            content_type = response.headers.get("Content-Type", "image/png")
        return body, content_type.split(";", maxsplit=1)[0] or "image/png"


def _uses_modern_protocol(model: str) -> bool:
    normalized = model.strip().lower()
    return normalized.startswith(("wan2.6", "wan2.7"))


def _uses_qwen_sync_protocol(model: str) -> bool:
    return model.strip().lower().startswith(("qwen-image-2.0", "qwen-image-3.0"))


def _qwen_reference_data_uris(references: list[str]) -> list[str]:
    materialized: list[str] = []
    for uri in references[:3]:
        body, mime_type = read_reference_image(uri)
        if len(body) > QWEN_MAX_REFERENCE_IMAGE_BYTES:
            raise ValueError(
                f"Qwen reference image exceeds {QWEN_MAX_REFERENCE_IMAGE_BYTES} bytes"
            )
        materialized.append(
            f"data:{mime_type};base64,{base64.b64encode(body).decode('ascii')}"
        )
    return materialized


def _api_url(base_url: str, endpoint: str) -> str:
    normalized_base = base_url.rstrip("/")
    normalized_endpoint = endpoint if endpoint.startswith("/") else f"/{endpoint}"
    if normalized_base.endswith("/api/v1") and normalized_endpoint.startswith("/api/v1/"):
        normalized_endpoint = normalized_endpoint.removeprefix("/api/v1")
    return f"{normalized_base}{normalized_endpoint}"


def _request_size(request: ProviderRequest, defaults: dict, *, model: str) -> str:
    raw_size = str(request.params.get("size", defaults.get("size", settings.image_size))).strip()
    normalized = raw_size.lower().replace("x", "*")
    if normalized in {"1280*720", "1024*576"}:
        # The project-wide 16:9 default is below wan2.6's minimum pixel area.
        if _uses_modern_protocol(model):
            return "1696*960"
    if "*" not in normalized:
        raise ValueError("DashScope image size must use WIDTHxHEIGHT or WIDTH*HEIGHT")
    width, height = normalized.split("*", maxsplit=1)
    if int(width) <= 0 or int(height) <= 0:
        raise ValueError("DashScope image dimensions must be positive")
    return f"{int(width)}*{int(height)}"


def _request_bool(params: dict, defaults: dict, name: str, fallback: bool) -> bool:
    value = params.get(name, defaults.get(name, fallback))
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
    raise ValueError(f"DashScope image parameter {name} must be a boolean")


def _task_id(payload: dict) -> str:
    output = payload.get("output")
    output = output if isinstance(output, dict) else {}
    task_id = output.get("task_id") or payload.get("task_id")
    if not task_id:
        raise ValueError(_error_message(payload, "DashScope image response did not include task_id"))
    return str(task_id)


def _request_id(payload: dict) -> str | None:
    raw_request_id = payload.get("request_id") or payload.get("requestId")
    return str(raw_request_id) if raw_request_id else None


def _task_status(payload: dict) -> str:
    output = payload.get("output")
    output = output if isinstance(output, dict) else {}
    raw_status = output.get("task_status") or payload.get("task_status") or payload.get("status") or ""
    normalized = str(raw_status).strip().lower()
    aliases = {
        "success": "succeeded",
        "completed": "succeeded",
        "done": "succeeded",
        "error": "failed",
        "canceled": "cancelled",
    }
    return aliases.get(normalized, normalized)


def _image_url(payload: dict) -> str | None:
    output = payload.get("output")
    output = output if isinstance(output, dict) else {}
    for choice in output.get("choices") or []:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        message = message if isinstance(message, dict) else {}
        for content in message.get("content") or []:
            if isinstance(content, dict) and content.get("image"):
                return str(content["image"])
    for result in output.get("results") or []:
        if not isinstance(result, dict):
            continue
        url = result.get("url") or result.get("image")
        if url:
            return str(url)
    return None


def _response_dimensions(
    payload: dict,
    request: ProviderRequest,
    defaults: dict,
    *,
    model: str,
) -> tuple[int, int]:
    usage = payload.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    output = payload.get("output")
    output = output if isinstance(output, dict) else {}
    output_usage = output.get("usage")
    output_usage = output_usage if isinstance(output_usage, dict) else {}
    size = usage.get("size") or output_usage.get("size")
    if not size:
        size = _request_size(request, defaults, model=model)
    try:
        width, height = str(size).lower().replace("x", "*").split("*", maxsplit=1)
        return int(width), int(height)
    except (TypeError, ValueError):
        return (1280, 720) if _uses_qwen_sync_protocol(model) else (1696, 960)


def _read_http_error(exc: HTTPError) -> dict:
    try:
        raw = exc.read().decode("utf-8")
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {"body": parsed}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {"status": exc.code, "reason": str(exc.reason)}


def _error_message(payload: dict, fallback: str) -> str:
    output = payload.get("output") if isinstance(payload, dict) else None
    output = output if isinstance(output, dict) else {}
    message = payload.get("message") if isinstance(payload, dict) else None
    return str(message or output.get("message") or output.get("task_message") or fallback)


def _failed_response(
    provider_task_id: str,
    code: str,
    message: str,
    raw_error: dict,
    retryable: bool,
) -> ProviderResponse:
    return ProviderResponse(
        status=ProviderStatus.FAILED,
        provider_task_id=provider_task_id,
        raw_response=raw_error,
        error=ProviderError(
            error_code=code,
            error_message=message,
            is_retryable=retryable,
            raw_error=raw_error,
        ),
    )


def build_dashscope_image_providers() -> list[ProviderAdapter]:
    return [DashScopeImageProvider()]
