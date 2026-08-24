import json
import re
from decimal import Decimal
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import NAMESPACE_URL, uuid4, uuid5

from app.core.config import settings
from app.providers.base import ProviderAdapter
from app.providers.image_quality import explicit_image_steps, image_quality
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


class OpenAICompatibleImageProvider(ProviderAdapter):
    name = "openai_image"
    type = ProviderType.IMAGE
    capabilities = ["text_to_image"]

    def __init__(
        self,
        *,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        default_params: dict[str, Any] | None = None,
    ) -> None:
        self.default_params = dict(default_params or {})
        self.model = model or settings.image_model
        self.api_key = api_key if api_key is not None else settings.image_api_key
        self.base_url = (base_url if base_url is not None else settings.image_base_url or "").rstrip("/")
        supports_references = (
            settings.image_supports_references
            if default_params is None
            else bool(self.default_params.get("supports_references", False))
        )
        if supports_references:
            self.capabilities = [*self.capabilities, "reference_payload"]

    def validate_config(self) -> None:
        if not self.base_url:
            raise ValueError("IMAGE_BASE_URL is required for openai_image provider")

    def submit(self, request: ProviderRequest) -> ProviderResponse:
        provider_task_id = str(uuid5(NAMESPACE_URL, f"{request.project_id}:{request.task_id}:openai-image"))
        try:
            self.validate_config()
            raw_response = self._generate(request)
        except HTTPError as exc:
            raw_error = _read_error_body(exc)
            return ProviderResponse(
                status=ProviderStatus.FAILED,
                provider_task_id=provider_task_id,
                raw_response=raw_error,
                error=ProviderError(
                    error_code="openai_image_http_error",
                    error_message=_extract_error_message(raw_error, str(exc)),
                    is_retryable=exc.code in {408, 429, 500, 502, 503, 504},
                    raw_error=raw_error,
                ),
            )
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            return ProviderResponse(
                status=ProviderStatus.FAILED,
                provider_task_id=provider_task_id,
                raw_response={},
                error=ProviderError(
                    error_code="openai_image_failed",
                    error_message=str(exc),
                    is_retryable=True,
                ),
            )

        response_size = request.params.get("size", self.default_params.get("size", settings.image_size))
        asset = self._extract_asset(raw_response, provider_task_id, response_size)
        if asset is None:
            return ProviderResponse(
                status=ProviderStatus.FAILED,
                provider_task_id=provider_task_id,
                raw_response=raw_response,
                error=ProviderError(
                    error_code="openai_image_missing_asset",
                    error_message="OpenAI-compatible image response did not include b64_json or url",
                    raw_error=raw_response,
                ),
            )

        return ProviderResponse(
            status=ProviderStatus.SUCCEEDED,
            provider_task_id=provider_task_id,
            assets=[asset],
            raw_response=raw_response,
            usage=ProviderUsage(cost=Decimal("0"), unit="USD"),
        )

    def _generate(self, request: ProviderRequest) -> dict:
        negative_transport = str(
            request.params.get(
                "negative_prompt_transport",
                self.default_params.get("negative_prompt_transport", "prompt"),
            )
        ).strip().lower()
        fold_negative_into_prompt = (
            negative_transport == "prompt"
            and str(request.metadata.get("entity_type") or "") in {"shot", "shot_frame"}
        )
        effective_prompt = (
            _with_negative_constraints(request.prompt, request.negative_prompt)
            if fold_negative_into_prompt
            else request.prompt
        )
        payload = {
            "model": request.model or self.model,
            "prompt": effective_prompt,
            "size": request.params.get("size", self.default_params.get("size", settings.image_size)),
            "quality": image_quality(request.params, str(self.default_params.get("quality", settings.image_quality))),
            "n": request.params.get("n", 1),
            "response_format": request.params.get(
                "response_format",
                self.default_params.get("response_format", settings.image_response_format),
            ),
            "output_format": request.params.get("output_format", "png"),
            "guidance_scale": request.params.get(
                "guidance_scale",
                self.default_params.get("guidance_scale", settings.image_guidance_scale),
            ),
        }
        steps = explicit_image_steps(
            request.params,
            self.default_params.get("steps", settings.image_num_inference_steps),
        )
        if steps is not None:
            payload["steps"] = steps
        seed = request.params.get("seed", settings.image_seed)
        if seed is not None:
            payload["seed"] = int(seed)
        for optional_field in ("output_compression", "background"):
            if optional_field in request.params:
                payload[optional_field] = request.params[optional_field]

        reference_transport = request.params.get(
            "asset_reference_transport",
            self.default_params.get("reference_transport", settings.image_reference_transport),
        )
        use_reference_edit = (
            "reference_payload" in self.capabilities
            and reference_transport in {"payload", "hybrid"}
            and request.references
        )

        headers: dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if use_reference_edit:
            endpoint = f"{self.base_url}/v1/images/edits"
            files = _reference_multipart_files(request.references)
            body, content_type = _encode_multipart(payload, files)
            headers["Content-Type"] = content_type
            http_request = Request(endpoint, data=body, headers=headers, method="POST")
        else:
            endpoint = f"{self.base_url}/v1/images/generations"
            headers["Content-Type"] = "application/json"
            http_request = Request(
                endpoint,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers=headers,
                method="POST",
            )
        with urlopen(http_request, timeout=settings.provider_timeout_sec) as response:
            raw_response = json.loads(response.read().decode("utf-8"))
        raw_response.setdefault(
            "request",
            {
                **payload,
                "endpoint": endpoint,
                "reference_count": len(files) if use_reference_edit else 0,
            },
        )
        if use_reference_edit:
            raw_response.setdefault(
                "request_reference_summary",
                {
                    "reference_count": len(request.references),
                    "reference_payload_count": len(files),
                    "reference_image_count": len(files),
                    "payload_format": "multipart/form-data",
                    "multipart_field": "image[]",
                    "truncated_count": max(0, len(request.references) - len(files)),
                },
            )
        return raw_response

    def _extract_asset(
        self,
        raw_response: dict,
        provider_task_id: str,
        response_size: str,
    ) -> ProviderAsset | None:
        data = raw_response.get("data")
        if not isinstance(data, list) or not data:
            return None

        first_asset = data[0]
        if not isinstance(first_asset, dict):
            return None

        width, height = _parse_size(response_size)
        b64_json = first_asset.get("b64_json")
        if b64_json:
            return ProviderAsset(
                asset_type="image",
                uri=f"data:image/png;base64,{b64_json}",
                mime_type="image/png",
                width=width,
                height=height,
                metadata={"provider_task_id": provider_task_id},
            )

        url = first_asset.get("url")
        if url:
            return ProviderAsset(
                asset_type="image",
                uri=url,
                mime_type="image/png",
                width=width,
                height=height,
                metadata={"provider_task_id": provider_task_id},
            )
        return None


def _with_negative_constraints(prompt: str, negative_prompt: str | None) -> str:
    """Carry useful exclusions through OpenAI-compatible image APIs.

    The standard images schema has no negative_prompt field. Keep this bounded
    and deduplicated so exclusions reach FLUX without recreating the prompt
    bloat that shot-level governance removed.
    """

    if not negative_prompt:
        return prompt
    normalized_prompt = _normalized_prompt_text(prompt)
    selected: list[str] = []
    selected_chars = 0
    for raw_item in re.split(r"[，,；;\n]+", negative_prompt):
        item = raw_item.strip()
        if not item or _normalized_prompt_text(item) in normalized_prompt:
            continue
        if len(selected) >= 12 or (selected and selected_chars + len(item) > 220):
            break
        selected.append(item)
        selected_chars += len(item)
    if not selected:
        return prompt
    return f"{prompt.rstrip()}\n补充排除（低于镜头内容优先级）：{'、'.join(selected)}"


def _normalized_prompt_text(value: str) -> str:
    return "".join(re.findall(r"[A-Za-z0-9\u4e00-\u9fff]", (value or "").lower()))


def _parse_size(size: str) -> tuple[int | None, int | None]:
    try:
        width, height = size.lower().split("x", maxsplit=1)
        return int(width), int(height)
    except (ValueError, AttributeError):
        return None, None


def _reference_multipart_files(references: list[str]) -> list[tuple[str, str, str, bytes]]:
    files: list[tuple[str, str, str, bytes]] = []
    for index, uri in enumerate(references[:4], start=1):
        body, mime_type = read_reference_image(uri)
        files.append(("image[]", f"reference-{index}{_image_extension(mime_type)}", mime_type, body))
    return files


def _image_extension(mime_type: str) -> str:
    return {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }.get(mime_type.lower(), ".png")


def _encode_multipart(
    fields: dict[str, Any],
    files: list[tuple[str, str, str, bytes]],
) -> tuple[bytes, str]:
    boundary = f"----avd-{uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("ascii"),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"),
                _form_value(value).encode("utf-8"),
                b"\r\n",
            ]
        )
    for field_name, filename, mime_type, body in files:
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("ascii"),
                (
                    f'Content-Disposition: form-data; name="{field_name}"; '
                    f'filename="{filename}"\r\n'
                ).encode("utf-8"),
                f"Content-Type: {mime_type}\r\n\r\n".encode("ascii"),
                body,
                b"\r\n",
            ]
        )
    chunks.append(f"--{boundary}--\r\n".encode("ascii"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _form_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


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


def build_openai_image_providers() -> list[ProviderAdapter]:
    return [OpenAICompatibleImageProvider()]
