from __future__ import annotations

import base64
import json
from decimal import Decimal
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen
from uuid import NAMESPACE_URL, UUID, uuid5

from app.core.config import settings
from app.providers.base import ProviderAdapter
from app.providers.types import (
    ProviderAsset,
    ProviderError,
    ProviderRequest,
    ProviderResponse,
    ProviderStatus,
    ProviderType,
    ProviderUsage,
)


class QwenImageMusubiProvider(ProviderAdapter):
    """Adapter for the resident Musubi service's native POST /generate contract."""

    name = "qwen_image_musubi"
    type = ProviderType.IMAGE
    capabilities = ["text_to_image", "prompt_reference_guidance"]

    def __init__(
        self,
        *,
        model: str | None = None,
        base_url: str | None = None,
        default_params: dict[str, Any] | None = None,
    ) -> None:
        self.model = model or settings.qwen_image_musubi_model
        self.base_url = (
            base_url
            if base_url is not None
            else settings.qwen_image_musubi_base_url or ""
        ).rstrip("/")
        self.default_params = dict(default_params or {})

    def validate_config(self) -> None:
        if not self.base_url:
            raise ValueError("IMAGE_BASE_URL is required for qwen_image_musubi provider")

    def submit(self, request: ProviderRequest) -> ProviderResponse:
        provider_task_id = str(
            uuid5(NAMESPACE_URL, f"{request.project_id}:{request.task_id}:qwen-image-musubi")
        )
        try:
            self.validate_config()
            raw_response, content_type, raw_body = self._generate(request, provider_task_id)
        except HTTPError as exc:
            raw_error = _read_error_body(exc)
            return _failed_response(
                provider_task_id,
                code="qwen_image_musubi_http_error",
                message=_extract_error_message(raw_error, str(exc)),
                raw_error=raw_error,
                retryable=exc.code in {408, 409, 429, 500, 502, 503, 504},
            )
        except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            return _failed_response(
                provider_task_id,
                code="qwen_image_musubi_failed",
                message=str(exc),
                raw_error={},
                retryable=True,
            )

        if str(raw_response.get("status", "")).strip().lower() in {"error", "failed"}:
            return _failed_response(
                provider_task_id,
                code="qwen_image_musubi_generation_failed",
                message=_extract_error_message(raw_response, "Musubi image generation failed"),
                raw_error=raw_response,
                retryable=True,
            )

        try:
            asset = self._extract_asset(
                raw_response,
                content_type=content_type,
                raw_body=raw_body,
                provider_task_id=provider_task_id,
                request=request,
            )
        except ValueError as exc:
            return _failed_response(
                provider_task_id,
                code="qwen_image_musubi_unpreviewable_result",
                message=str(exc),
                raw_error=raw_response,
                retryable=False,
            )
        if asset is None:
            return _failed_response(
                provider_task_id,
                code="qwen_image_musubi_missing_asset",
                message=(
                    "Musubi response did not include image bytes, base64, or a public URL. "
                    "A server-local output path cannot be previewed by this project."
                ),
                raw_error=raw_response,
                retryable=False,
            )

        return ProviderResponse(
            status=ProviderStatus.SUCCEEDED,
            provider_task_id=provider_task_id,
            assets=[asset],
            raw_response=raw_response,
            usage=ProviderUsage(cost=Decimal("0"), unit="USD"),
        )

    def _generate(
        self,
        request: ProviderRequest,
        provider_task_id: str,
    ) -> tuple[dict[str, Any], str, bytes]:
        width, height = _request_size(request, self.default_params)
        steps = _positive_int(
            request.params.get("steps", self.default_params.get("steps", 8)),
            field="steps",
            minimum=1,
            maximum=100,
        )
        cfg = float(
            request.params.get(
                "cfg",
                request.params.get(
                    "guidance_scale",
                    self.default_params.get("guidance_scale", self.default_params.get("cfg", 1.0)),
                ),
            )
        )
        if cfg < 0:
            raise ValueError("cfg must be greater than or equal to 0")
        seed = request.params.get("seed", self.default_params.get("seed", settings.image_seed))
        if seed is None:
            seed = _seed_from_provider_task_id(provider_task_id)

        payload = {
            "id": str(request.params.get("id") or f"avd_{provider_task_id.replace('-', '')}"),
            "prompt": request.prompt,
            "seed": int(seed),
            "width": width,
            "height": height,
            "steps": steps,
            "cfg": cfg,
        }
        endpoint = str(self.default_params.get("endpoint") or "/generate").strip()
        if not endpoint.startswith("/"):
            endpoint = f"/{endpoint}"
        http_request = Request(
            f"{self.base_url}{endpoint}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(http_request, timeout=settings.provider_timeout_sec) as response:
            content_type = response.headers.get("Content-Type", "")
            raw_body = response.read()

        if content_type.startswith("image/"):
            return {
                "status": "ok",
                "content_type": content_type,
                "bytes": len(raw_body),
                "request": payload,
            }, content_type, raw_body
        raw_response = json.loads(raw_body.decode("utf-8"))
        if not isinstance(raw_response, dict):
            raise ValueError("Musubi response must be a JSON object or image bytes")
        raw_response.setdefault("request", payload)
        return raw_response, content_type, raw_body

    def _extract_asset(
        self,
        raw_response: dict[str, Any],
        *,
        content_type: str,
        raw_body: bytes,
        provider_task_id: str,
        request: ProviderRequest,
    ) -> ProviderAsset | None:
        width, height = _response_size(raw_response, request, self.default_params)
        metadata = {
            "provider_task_id": provider_task_id,
            "musubi_request_id": _nested_string(raw_response, ("id", "request_id", "job_id")),
        }
        if content_type.startswith("image/"):
            mime_type = content_type.split(";", maxsplit=1)[0]
            return ProviderAsset(
                asset_type="image",
                uri=f"data:{mime_type};base64,{base64.b64encode(raw_body).decode('ascii')}",
                mime_type=mime_type,
                width=width,
                height=height,
                metadata=metadata,
            )

        b64_value = _nested_string(
            raw_response,
            ("b64_json", "base64", "image_base64", "image_b64"),
        )
        if b64_value:
            uri = b64_value if b64_value.startswith("data:image/") else f"data:image/png;base64,{b64_value}"
            return ProviderAsset(
                asset_type="image",
                uri=uri,
                mime_type=_nested_string(raw_response, ("mime_type", "content_type")) or "image/png",
                width=width,
                height=height,
                metadata=metadata,
            )

        image_value = _nested_string(raw_response, ("image",))
        if image_value and image_value.startswith("data:image/"):
            return ProviderAsset(
                asset_type="image",
                uri=image_value,
                mime_type=image_value.removeprefix("data:").split(";", maxsplit=1)[0] or "image/png",
                width=width,
                height=height,
                metadata=metadata,
            )

        url = _nested_string(raw_response, ("url", "image_url", "output_url", "uri"))
        if url:
            if url.startswith("data:image/"):
                return ProviderAsset(
                    asset_type="image",
                    uri=url,
                    mime_type=url.removeprefix("data:").split(";", maxsplit=1)[0] or "image/png",
                    width=width,
                    height=height,
                    metadata=metadata,
                )
            public_url = (
                url
                if url.startswith(("http://", "https://"))
                else urljoin(f"{self.base_url}/", url)
            )
            try:
                image_body, mime_type = _download_image(public_url)
            except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
                raise ValueError(f"Could not download the Musubi result URL {public_url}: {exc}") from exc
            return ProviderAsset(
                asset_type="image",
                uri=f"data:{mime_type};base64,{base64.b64encode(image_body).decode('ascii')}",
                mime_type=mime_type,
                width=width,
                height=height,
                metadata={**metadata, "remote_url": public_url},
            )

        path = _nested_string(raw_response, ("path", "file_path", "output_path", "image_path"))
        if path:
            raise ValueError(
                f"Musubi only returned a server-local path ({path[:160]}). "
                "Configure the service to return base64/image bytes or a public URL."
            )
        return None


def _request_size(request: ProviderRequest, default_params: dict[str, Any]) -> tuple[int, int]:
    size = str(request.params.get("size", default_params.get("size", settings.image_size)))
    try:
        width_text, height_text = size.lower().split("x", maxsplit=1)
        width = _positive_int(width_text, field="width", minimum=64, maximum=4096)
        height = _positive_int(height_text, field="height", minimum=64, maximum=4096)
    except (AttributeError, ValueError) as exc:
        raise ValueError(f"Invalid Musubi image size: {size!r}; expected WIDTHxHEIGHT") from exc
    return width, height


def _response_size(
    raw_response: dict[str, Any],
    request: ProviderRequest,
    default_params: dict[str, Any],
) -> tuple[int, int]:
    fallback_width, fallback_height = _request_size(request, default_params)
    width = _nested_value(raw_response, "width")
    height = _nested_value(raw_response, "height")
    try:
        return int(width or fallback_width), int(height or fallback_height)
    except (TypeError, ValueError):
        return fallback_width, fallback_height


def _positive_int(
    value: object,
    *,
    field: str,
    minimum: int,
    maximum: int,
) -> int:
    parsed = int(value)
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    return parsed


def _seed_from_provider_task_id(provider_task_id: str) -> int:
    return UUID(provider_task_id).int % ((1 << 31) - 1) + 1


def _download_image(url: str) -> tuple[bytes, str]:
    http_request = Request(url, method="GET")
    with urlopen(http_request, timeout=settings.provider_timeout_sec) as response:
        content_type = response.headers.get("Content-Type", "").split(";", maxsplit=1)[0]
        body = response.read()
    if not content_type.startswith("image/"):
        raise ValueError(f"unexpected content type {content_type or '<missing>'}")
    if not body:
        raise ValueError("empty image response")
    if len(body) > 50 * 1024 * 1024:
        raise ValueError("image response exceeds 50 MiB")
    return body, content_type


def _nested_string(raw_response: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = _nested_value(raw_response, key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _nested_value(value: object, key: str) -> object | None:
    if isinstance(value, dict):
        if key in value:
            return value[key]
        for nested_key in ("data", "result", "output", "details"):
            if nested_key in value:
                found = _nested_value(value[nested_key], key)
                if found is not None:
                    return found
    if isinstance(value, list):
        for item in value:
            found = _nested_value(item, key)
            if found is not None:
                return found
    return None


def _failed_response(
    provider_task_id: str,
    *,
    code: str,
    message: str,
    raw_error: dict[str, Any],
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


def _read_error_body(exc: HTTPError) -> dict[str, Any]:
    raw_text = exc.read().decode("utf-8", errors="replace")
    try:
        body = json.loads(raw_text)
    except json.JSONDecodeError:
        body = {"raw_text": raw_text}
    if not isinstance(body, dict):
        body = {"raw_response": body}
    body.setdefault("status_code", exc.code)
    return body


def _extract_error_message(body: dict[str, Any], fallback: str) -> str:
    error = body.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or fallback)
    if error:
        return str(error)
    details = body.get("details")
    if isinstance(details, dict):
        return str(details.get("message") or details.get("error") or fallback)
    if details:
        return str(details)
    return str(body.get("message") or body.get("detail") or body.get("raw_text") or fallback)


def build_qwen_image_musubi_providers() -> list[ProviderAdapter]:
    return [QwenImageMusubiProvider()]
