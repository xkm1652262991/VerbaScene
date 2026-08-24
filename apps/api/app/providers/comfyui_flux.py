import base64
import json
import time
from decimal import Decimal
from itertools import count
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
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


_endpoint_counter = count()
_endpoint_lock = Lock()


class ComfyUIFluxProvider(ProviderAdapter):
    name = "comfyui_flux"
    type = ProviderType.IMAGE
    capabilities = ["text_to_image"]

    def __init__(self, *, model: str | None = None, base_url: str | None = None) -> None:
        self.model = model or settings.comfyui_flux_model or settings.image_model
        self.base_urls = _parse_base_urls(base_url if base_url is not None else settings.comfyui_flux_base_urls)

    def validate_config(self) -> None:
        if not self.base_urls:
            raise ValueError("COMFYUI_FLUX_BASE_URLS is required for comfyui_flux provider")

    def submit(self, request: ProviderRequest) -> ProviderResponse:
        provider_task_id = str(
            uuid5(NAMESPACE_URL, f"{request.project_id}:{request.task_id}:comfyui-flux")
        )
        try:
            self.validate_config()
            endpoint = self._select_endpoint(request)
            prompt_response = self._queue_prompt(endpoint, request)
            comfy_prompt_id = str(prompt_response["prompt_id"])
            provider_task_id = f"comfyui:{comfy_prompt_id}"
            history = self._wait_for_history(endpoint, comfy_prompt_id)
            asset = self._extract_asset(endpoint, history, provider_task_id, request)
        except HTTPError as exc:
            raw_error = _read_error_body(exc)
            return _failed_response(
                provider_task_id,
                "comfyui_flux_http_error",
                _extract_error_message(raw_error, str(exc)),
                raw_error,
                exc.code in {408, 429, 500, 502, 503, 504},
            )
        except (URLError, TimeoutError, ValueError, KeyError, json.JSONDecodeError) as exc:
            return _failed_response(
                provider_task_id,
                "comfyui_flux_failed",
                str(exc),
                {},
                True,
            )

        if asset is None:
            return _failed_response(
                provider_task_id,
                "comfyui_flux_missing_asset",
                "ComfyUI history did not include an output image",
                history,
                False,
            )

        return ProviderResponse(
            status=ProviderStatus.SUCCEEDED,
            provider_task_id=provider_task_id,
            assets=[asset],
            raw_response={
                "endpoint": endpoint,
                "prompt_id": provider_task_id.removeprefix("comfyui:"),
                "history": _compact_history(history),
            },
            usage=ProviderUsage(cost=Decimal("0"), unit="USD"),
        )

    def _select_endpoint(self, request: ProviderRequest) -> str:
        if len(self.base_urls) == 1:
            return self.base_urls[0]
        requested_endpoint = request.params.get("comfyui_endpoint")
        if requested_endpoint:
            endpoint = str(requested_endpoint).rstrip("/")
            if endpoint in self.base_urls:
                return endpoint
        with _endpoint_lock:
            index = next(_endpoint_counter)
        return self.base_urls[index % len(self.base_urls)]

    def _queue_prompt(self, endpoint: str, request: ProviderRequest) -> dict:
        payload = {
            "client_id": str(
                uuid5(NAMESPACE_URL, f"{request.project_id}:{request.task_id}:comfyui-client")
            ),
            "prompt": self._build_prompt(request),
        }
        http_request = Request(
            f"{endpoint}/prompt",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(http_request, timeout=settings.provider_timeout_sec) as response:
            return json.loads(response.read().decode("utf-8"))

    def _wait_for_history(self, endpoint: str, prompt_id: str) -> dict:
        deadline = time.monotonic() + settings.comfyui_flux_job_timeout_sec
        poll_interval = max(settings.comfyui_flux_poll_interval_sec, 1)
        while time.monotonic() < deadline:
            with urlopen(
                f"{endpoint}/history/{prompt_id}",
                timeout=settings.provider_timeout_sec,
            ) as response:
                history_response = json.loads(response.read().decode("utf-8"))
            history = history_response.get(prompt_id)
            if history:
                status = history.get("status", {})
                if status.get("status_str") in {"error", "failed"}:
                    raise ValueError(status.get("messages") or "ComfyUI workflow failed")
                if status.get("completed") or history.get("outputs"):
                    return history
            time.sleep(poll_interval)
        raise TimeoutError(f"ComfyUI prompt {prompt_id} timed out")

    def _build_prompt(self, request: ProviderRequest) -> dict:
        width, height = _parse_size(str(request.params.get("size", settings.image_size)))
        steps = resolved_image_steps(
            request.params,
            default_steps=settings.image_num_inference_steps,
            default_quality=settings.image_quality,
        )
        guidance = float(
            request.params.get(
                "guidance",
                request.params.get("guidance_scale", settings.image_guidance_scale),
            )
        )
        seed = int(request.params.get("seed", settings.image_seed or time.time_ns() % 2147483647))
        prompt_text = _merge_prompt(request.prompt, request.negative_prompt or request.params.get("negative_prompt"))
        filename_prefix = request.params.get("filename_prefix") or (
            f"flux_{request.project_id}_{request.task_id}".replace(":", "_")
        )

        return {
            "1": {
                "class_type": "UNETLoader",
                "inputs": {
                    "unet_name": self.model,
                    "weight_dtype": settings.comfyui_flux_weight_dtype,
                },
            },
            "2": {
                "class_type": "DualCLIPLoader",
                "inputs": {
                    "clip_name1": settings.comfyui_flux_clip_l,
                    "clip_name2": settings.comfyui_flux_t5xxl,
                    "type": "flux",
                    "device": settings.comfyui_flux_clip_device,
                },
            },
            "3": {
                "class_type": "VAELoader",
                "inputs": {"vae_name": settings.comfyui_flux_vae},
            },
            "4": {
                "class_type": "CLIPTextEncodeFlux",
                "inputs": {
                    "clip": ["2", 0],
                    "clip_l": prompt_text,
                    "t5xxl": prompt_text,
                    "guidance": guidance,
                },
            },
            "5": {
                "class_type": "EmptySD3LatentImage",
                "inputs": {"width": width, "height": height, "batch_size": 1},
            },
            "6": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
            "7": {
                "class_type": "BasicGuider",
                "inputs": {"model": ["1", 0], "conditioning": ["4", 0]},
            },
            "8": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": settings.comfyui_flux_sampler}},
            "9": {
                "class_type": "BasicScheduler",
                "inputs": {
                    "model": ["1", 0],
                    "scheduler": settings.comfyui_flux_scheduler,
                    "steps": steps,
                    "denoise": float(request.params.get("denoise", 1.0)),
                },
            },
            "10": {
                "class_type": "SamplerCustomAdvanced",
                "inputs": {
                    "noise": ["6", 0],
                    "guider": ["7", 0],
                    "sampler": ["8", 0],
                    "sigmas": ["9", 0],
                    "latent_image": ["5", 0],
                },
            },
            "11": {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["10", 0], "vae": ["3", 0]},
            },
            "12": {
                "class_type": "SaveImage",
                "inputs": {"images": ["11", 0], "filename_prefix": filename_prefix},
            },
        }

    def _extract_asset(
        self,
        endpoint: str,
        history: dict,
        provider_task_id: str,
        request: ProviderRequest,
    ) -> ProviderAsset | None:
        output_image = _first_output_image(history)
        if output_image is None:
            return None
        image_bytes = _download_output_image(endpoint, output_image)
        width, height = _parse_size(str(request.params.get("size", settings.image_size)))
        return ProviderAsset(
            asset_type="image",
            uri=f"data:image/png;base64,{base64.b64encode(image_bytes).decode('ascii')}",
            mime_type="image/png",
            width=width,
            height=height,
            metadata={
                "provider_task_id": provider_task_id,
                "endpoint": endpoint,
                "comfyui_output": output_image,
                "references_ignored": bool(request.references),
            },
        )


def _parse_base_urls(raw_value: str | None) -> list[str]:
    if not raw_value:
        return []
    return [value.strip().rstrip("/") for value in raw_value.split(",") if value.strip()]


def _parse_size(size: str) -> tuple[int, int]:
    try:
        width, height = size.lower().split("x", maxsplit=1)
        return int(width), int(height)
    except (ValueError, AttributeError):
        return 1280, 720


def _merge_prompt(prompt: str, negative_prompt: str | None) -> str:
    if not negative_prompt:
        return prompt
    return f"{prompt}\nAvoid: {negative_prompt}"


def _first_output_image(history: dict) -> dict | None:
    outputs = history.get("outputs")
    if not isinstance(outputs, dict):
        return None
    for output in outputs.values():
        images = output.get("images") if isinstance(output, dict) else None
        if isinstance(images, list) and images:
            first = images[0]
            if isinstance(first, dict) and first.get("filename"):
                return first
    return None


def _download_output_image(endpoint: str, image: dict) -> bytes:
    query = urlencode(
        {
            "filename": image.get("filename", ""),
            "subfolder": image.get("subfolder", ""),
            "type": image.get("type", "output"),
        }
    )
    with urlopen(f"{endpoint}/view?{query}", timeout=settings.provider_timeout_sec) as response:
        return response.read()


def _compact_history(history: dict) -> dict:
    return {
        "status": history.get("status"),
        "outputs": history.get("outputs"),
    }


def _failed_response(
    provider_task_id: str,
    error_code: str,
    error_message: str,
    raw_error: dict,
    is_retryable: bool,
) -> ProviderResponse:
    return ProviderResponse(
        status=ProviderStatus.FAILED,
        provider_task_id=provider_task_id,
        raw_response=raw_error,
        error=ProviderError(
            error_code=error_code,
            error_message=error_message,
            is_retryable=is_retryable,
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


def build_comfyui_flux_providers() -> list[ProviderAdapter]:
    return [ComfyUIFluxProvider()]
