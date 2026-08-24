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
from app.providers.types import (
    ProviderAsset,
    ProviderError,
    ProviderRequest,
    ProviderResponse,
    ProviderStatus,
    ProviderType,
    ProviderUsage,
)


class GeminiImageProvider(ProviderAdapter):
    name = "gemini"
    type = ProviderType.IMAGE
    capabilities = ["text_to_image", "reference_to_image"]

    def __init__(
        self,
        *,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self.model = model or settings.gemini_image_model or settings.image_model
        self.api_key = api_key if api_key is not None else settings.gemini_api_key or settings.image_api_key
        self.base_url = (base_url or settings.gemini_base_url).rstrip("/")

    def validate_config(self) -> None:
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY or IMAGE_API_KEY is required for Gemini image provider")

    def submit(self, request: ProviderRequest) -> ProviderResponse:
        provider_task_id = str(uuid5(NAMESPACE_URL, f"{request.project_id}:{request.task_id}:gemini-image"))
        try:
            self.validate_config()
            raw_response = self._generate(request)
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            return ProviderResponse(
                status=ProviderStatus.FAILED,
                provider_task_id=provider_task_id,
                raw_response={},
                error=ProviderError(
                    error_code="gemini_image_failed",
                    error_message=str(exc),
                    is_retryable=True,
                ),
            )

        asset = self._extract_asset(raw_response, provider_task_id)
        if asset is None:
            return ProviderResponse(
                status=ProviderStatus.FAILED,
                provider_task_id=provider_task_id,
                raw_response=raw_response,
                error=ProviderError(
                    error_code="gemini_image_missing_asset",
                    error_message="Gemini response did not include image data",
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
        payload = {
            "contents": [
                {
                    "parts": _request_parts(request),
                }
            ],
            "generationConfig": {
                "responseModalities": ["TEXT", "IMAGE"],
            },
        }
        url = f"{self.base_url}/models/{self.model}:generateContent"
        http_request = Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-goog-api-key": self.api_key or "",
            },
            method="POST",
        )
        with urlopen(http_request, timeout=settings.provider_timeout_sec) as response:
            return json.loads(response.read().decode("utf-8"))

    def _extract_asset(self, raw_response: dict, provider_task_id: str) -> ProviderAsset | None:
        for candidate in raw_response.get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                inline_data = part.get("inlineData") or part.get("inline_data")
                if not inline_data:
                    continue
                mime_type = inline_data.get("mimeType") or inline_data.get("mime_type") or "image/png"
                data = inline_data.get("data")
                if not data:
                    continue
                return ProviderAsset(
                    asset_type="image",
                    uri=f"data:{mime_type};base64,{data}",
                    mime_type=mime_type,
                    width=1280,
                    height=720,
                    metadata={"provider_task_id": provider_task_id},
                )
        return None


def build_gemini_providers() -> list[ProviderAdapter]:
    return [GeminiImageProvider()]


def _request_parts(request: ProviderRequest) -> list[dict]:
    parts: list[dict] = [{"text": request.prompt}]
    for uri in request.references[:6]:
        try:
            body, mime_type = _read_reference_image(uri)
        except (ValueError, OSError, HTTPError, URLError, TimeoutError):
            continue
        parts.append(
            {
                "inline_data": {
                    "mime_type": mime_type,
                    "data": base64.b64encode(body).decode("ascii"),
                }
            }
        )
    return parts


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
