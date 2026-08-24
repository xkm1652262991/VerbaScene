import base64
import json
import mimetypes
from decimal import Decimal
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import NAMESPACE_URL, uuid5

from app.core.config import settings
from app.providers.base import ProviderAdapter
from app.providers.image_quality import resolved_image_steps
from app.providers.types import (
    ProviderAsset,
    ProviderError,
    ProviderRequest,
    ProviderResponse,
    ProviderStatus,
    ProviderType,
    ProviderUsage,
)


class CustomImageHTTPProvider(ProviderAdapter):
    name = "custom_image_http"
    type = ProviderType.IMAGE
    capabilities = ["text_to_image", "prompt_reference_guidance"]

    def __init__(
        self,
        *,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        default_params: dict | None = None,
    ) -> None:
        self.default_params = dict(default_params or {})
        self.model = model or settings.image_model
        self.base_url = (base_url if base_url is not None else settings.image_base_url or "").rstrip("/")
        self.api_key = api_key if api_key is not None else settings.image_api_key
        supports_references = (
            settings.image_supports_references
            if default_params is None
            else bool(self.default_params.get("supports_references", False))
        )
        if supports_references:
            self.capabilities = [*self.capabilities, "reference_payload"]

    def validate_config(self) -> None:
        if not self.base_url:
            raise ValueError("IMAGE_BASE_URL is required for custom_image_http provider")

    def submit(self, request: ProviderRequest) -> ProviderResponse:
        provider_task_id = str(uuid5(NAMESPACE_URL, f"{request.project_id}:{request.task_id}:custom-image-http"))
        try:
            self.validate_config()
            raw_response, content_type, raw_body = self._generate(request)
        except HTTPError as exc:
            raw_error = _read_error_body(exc)
            return _failed_response(
                provider_task_id,
                "custom_image_http_error",
                _extract_error_message(raw_error, str(exc)),
                raw_error,
                exc.code in {408, 429, 500, 502, 503, 504},
            )
        except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            return _failed_response(provider_task_id, "custom_image_http_failed", str(exc), {}, True)

        try:
            asset = self._extract_asset(raw_response, content_type, raw_body, provider_task_id, request)
        except ValueError as exc:
            return _failed_response(
                provider_task_id,
                "custom_image_http_unpreviewable_path",
                str(exc),
                raw_response,
                False,
            )
        if asset is None:
            return _failed_response(
                provider_task_id,
                "custom_image_http_missing_asset",
                "Custom image response did not include image bytes, base64, url, or path",
                raw_response,
                False,
            )

        return ProviderResponse(
            status=ProviderStatus.SUCCEEDED,
            provider_task_id=provider_task_id,
            assets=[asset],
            raw_response=raw_response,
            usage=ProviderUsage(cost=Decimal("0"), unit="USD"),
        )

    def _generate(self, request: ProviderRequest) -> tuple[dict, str, bytes]:
        width, height = _request_size(request)
        payload = {
            "prompt": request.prompt,
            "negative_prompt": request.negative_prompt or request.params.get("negative_prompt") or "",
            "width": width,
            "height": height,
            "response_format": self.default_params.get("response_format", settings.image_response_format),
            "return_base64": self.default_params.get("response_format", settings.image_response_format)
            in {"b64_json", "base64", "image_base64"},
            "steps": resolved_image_steps(
                request.params,
                default_steps=self.default_params.get("steps", settings.image_num_inference_steps),
                default_quality=str(self.default_params.get("quality", settings.image_quality)),
            ),
            "true_cfg_scale": float(
                request.params.get(
                    "true_cfg_scale",
                    request.params.get(
                        "guidance_scale",
                        self.default_params.get("guidance_scale", settings.image_guidance_scale),
                    ),
                )
            ),
        }
        seed = request.params.get("seed", settings.image_seed)
        if seed is not None:
            payload["seed"] = int(seed)
        reference_transport = request.params.get(
            "asset_reference_transport",
            self.default_params.get("reference_transport", settings.image_reference_transport),
        )
        reference_metadata = request.params.get("asset_reference_metadata", {})
        if "reference_payload" in self.capabilities and reference_transport in {"payload", "hybrid"} and request.references:
            reference_payloads, reference_warnings = _reference_image_payloads(request)
            payload["references"] = request.references
            payload["reference_metadata"] = reference_metadata
            payload["reference_images"] = reference_payloads
            if reference_warnings:
                payload["reference_warnings"] = reference_warnings

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        http_request = Request(
            f"{self.base_url}{self.default_params.get('endpoint', settings.custom_image_endpoint)}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urlopen(http_request, timeout=settings.provider_timeout_sec) as response:
            content_type = response.headers.get("Content-Type", "")
            raw_body = response.read()

        if content_type.startswith("image/"):
            return {
                "content_type": content_type,
                "bytes": len(raw_body),
                "request_reference_summary": _reference_summary(payload),
                "request": _request_echo(payload),
            }, content_type, raw_body
        raw_response = json.loads(raw_body.decode("utf-8"))
        raw_response.setdefault(
            "request_reference_summary",
            _reference_summary(payload),
        )
        raw_response.setdefault("request", _request_echo(payload))
        return raw_response, content_type, raw_body

    def _extract_asset(
        self,
        raw_response: dict,
        content_type: str,
        raw_body: bytes,
        provider_task_id: str,
        request: ProviderRequest,
    ) -> ProviderAsset | None:
        width, height = _request_size(request)
        if content_type.startswith("image/"):
            mime_type = content_type.split(";", maxsplit=1)[0]
            return ProviderAsset(
                asset_type="image",
                uri=f"data:{mime_type};base64,{base64.b64encode(raw_body).decode('ascii')}",
                mime_type=mime_type,
                width=width,
                height=height,
                metadata={"provider_task_id": provider_task_id},
            )

        b64_value = _first_string(raw_response, ("b64_json", "base64", "image", "image_base64"))
        if b64_value:
            if b64_value.startswith("data:image/"):
                uri = b64_value
            else:
                uri = f"data:image/png;base64,{b64_value}"
            return ProviderAsset(
                asset_type="image",
                uri=uri,
                mime_type="image/png",
                width=int(raw_response.get("width") or width),
                height=int(raw_response.get("height") or height),
                metadata={"provider_task_id": provider_task_id},
            )

        url = _first_string(raw_response, ("url", "image_url", "uri"))
        if url:
            return ProviderAsset(
                asset_type="image",
                uri=url,
                mime_type="image/png",
                width=int(raw_response.get("width") or width),
                height=int(raw_response.get("height") or height),
                metadata={"provider_task_id": provider_task_id},
            )

        path = _first_string(raw_response, ("path", "file_path", "output_path"))
        if path:
            uri = _path_to_public_uri(path)
            if uri is None:
                raise ValueError(
                    "图片服务只返回了内部文件路径，前端无法预览。请让图片服务返回 base64/url，"
                    "或配置 CUSTOM_IMAGE_PUBLIC_BASE_URL 映射 CUSTOM_IMAGE_OUTPUT_DIR。"
                )
            return ProviderAsset(
                asset_type="image",
                uri=uri,
                mime_type="image/png",
                width=int(raw_response.get("width") or width),
                height=int(raw_response.get("height") or height),
                metadata={
                    "provider_task_id": provider_task_id,
                    "remote_path": path,
                    "requires_public_mapping": False,
                },
            )
        return None


def _request_size(request: ProviderRequest) -> tuple[int, int]:
    size = str(request.params.get("size", settings.image_size))
    try:
        width, height = size.lower().split("x", maxsplit=1)
        return int(width), int(height)
    except (ValueError, AttributeError):
        return 1280, 720


def _path_to_public_uri(path: str) -> str | None:
    if path.startswith(("http://", "https://", "data:image/")):
        return path
    public_base_url = (settings.custom_image_public_base_url or "").rstrip("/")
    output_dir = settings.custom_image_output_dir.rstrip("/")
    if public_base_url and path.startswith(f"{output_dir}/"):
        relative_path = path.removeprefix(f"{output_dir}/").lstrip("/")
        return f"{public_base_url}/{relative_path}"
    return None


def _reference_image_payloads(request: ProviderRequest) -> tuple[list[dict], list[dict]]:
    reference_metadata = request.metadata.get("asset_resolution") if isinstance(request.metadata, dict) else None
    reference_assets = reference_metadata.get("reference_assets") if isinstance(reference_metadata, dict) else []
    by_uri = {
        item.get("uri"): item
        for item in reference_assets
        if isinstance(item, dict) and isinstance(item.get("uri"), str)
    }
    payloads: list[dict] = []
    warnings: list[dict] = []
    for index, uri in enumerate(request.references[:6]):
        source = by_uri.get(uri, {})
        item = {
            "index": index,
            "uri": uri,
            "asset_id": source.get("asset_id"),
            "reference_role": source.get("reference_role"),
            "asset_role": source.get("asset_role"),
            "entity_type": source.get("entity_type"),
            "entity_id": source.get("entity_id"),
        }
        try:
            body, mime_type = _read_reference_image(uri)
            item["mime_type"] = mime_type
            item["b64_json"] = base64.b64encode(body).decode("ascii")
            item["bytes"] = len(body)
        except (ValueError, OSError, HTTPError, URLError, TimeoutError) as exc:
            warnings.append(
                {
                    "index": index,
                    "uri": uri[:160],
                    "reference_role": item.get("reference_role"),
                    "message": str(exc)[:240],
                }
            )
        payloads.append(item)
    return payloads, warnings


def _read_reference_image(uri: str) -> tuple[bytes, str]:
    if uri.startswith("data:image/") and ";base64," in uri:
        header, encoded = uri.split(",", 1)
        mime_type = header.removeprefix("data:").split(";", maxsplit=1)[0] or "image/png"
        return base64.b64decode(encoded), mime_type
    local_path = _local_storage_uri_path(uri)
    if local_path is not None:
        mime_type = mimetypes.guess_type(local_path.name)[0] or "image/png"
        return local_path.read_bytes(), mime_type
    if uri.startswith(("http://", "https://")):
        with urlopen(uri, timeout=settings.provider_timeout_sec) as response:
            body = response.read()
            mime_type = response.headers.get("Content-Type") or mimetypes.guess_type(uri)[0] or "image/png"
            return body, mime_type.split(";", maxsplit=1)[0]
    raise ValueError(f"Unsupported reference image URI: {uri[:48]}")


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


def _request_echo(payload: dict) -> dict:
    return {
        key: _request_echo_value(key, value)
        for key, value in payload.items()
    }


def _request_echo_value(key: str, value: object) -> object:
    if key == "references" and isinstance(value, list):
        return [
            f"<omitted data image {len(item)} chars>"
            if isinstance(item, str) and item.startswith("data:image/")
            else item
            for item in value
        ]
    if key == "reference_images" and isinstance(value, list):
        return [
            {
                image_key: (f"<omitted {len(image_value)} chars>" if image_key == "b64_json" and isinstance(image_value, str) else image_value)
                for image_key, image_value in item.items()
            }
            for item in value
            if isinstance(item, dict)
        ]
    if key in {"b64_json", "base64", "image", "image_base64"} and isinstance(value, str):
        return f"<omitted {len(value)} chars>"
    return value


def _reference_summary(payload: dict) -> dict:
    reference_images = payload.get("reference_images")
    reference_image_items = [item for item in reference_images or [] if isinstance(item, dict)]
    return {
        "reference_count": len(payload.get("references") or []),
        "reference_payload_count": len(reference_image_items),
        "reference_image_count": sum(1 for item in reference_image_items if isinstance(item.get("b64_json"), str) and item.get("b64_json")),
        "reference_roles": [
            item.get("reference_role")
            for item in reference_image_items
            if item.get("reference_role")
        ],
        "reference_warnings": payload.get("reference_warnings") or [],
    }


def _first_string(raw_response: dict, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = raw_response.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    data = raw_response.get("data")
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return _first_string(data[0], keys)
    return None


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


def _read_error_body(exc: HTTPError) -> dict:
    raw_text = exc.read().decode("utf-8", errors="replace")
    try:
        body = json.loads(raw_text)
    except json.JSONDecodeError:
        body = {"raw_text": raw_text}
    body.setdefault("status_code", exc.code)
    return body


def _extract_error_message(body: dict, fallback: str) -> str:
    error = body.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or fallback)
    if error:
        return str(error)
    detail = body.get("detail")
    if detail:
        return str(detail)
    raw_text = body.get("raw_text")
    if raw_text:
        return str(raw_text)
    return fallback


def build_custom_image_http_providers() -> list[ProviderAdapter]:
    return [CustomImageHTTPProvider()]
