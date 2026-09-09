from __future__ import annotations

import base64
import binascii
import json
import mimetypes
import re
import tempfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import NAMESPACE_URL, uuid4, uuid5

from app.core.config import settings
from app.platform.media import get_media_store
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


R2V_MODEL = "minimax-h3-r2v"
FL2VA_MODEL = "minimax-h3-fl2va"
AUTO_MODELS = {"", "auto", "minimax-h3-auto", "minimax_h3_auto"}
MAX_REFERENCE_IMAGES = 9
MAX_IMAGE_BYTES = 50 * 1024 * 1024
H3_AUDIO_PROMPT_VERSION = "h3-native-audio-v1"

_GENERIC_AUDIO_INTRO = (
    "生成连续动画英语短剧片段，使用原生英文对白、环境音和动作音效。"
)
_H3_CORE_FIELDS = (
    "integrated_multimodal_description",
    "overall_soundscape",
    "non_diegetic_music",
)
_H3_FULL_REFERENCE_FIELDS = (
    "subject_definitions",
    "summary",
    "retention_analysis",
    "detailed_description",
    "overall_soundscape",
    "non_diegetic_music",
)
_DIALOGUE_WITH_EMOTION_RE = re.compile(
    r"^(?P<speaker>.+?)（(?P<emotion>[^）]+)）说：\{(?P<text>.*)\}$"
)
_DIALOGUE_RE = re.compile(r"^(?P<speaker>.+?)说：\{(?P<text>.*)\}$")
_VOICEOVER_RE = re.compile(
    r"旁白|画外音|解说|系统语音|引导声|narrator|voice[ -]?over",
    re.IGNORECASE,
)
_SPEECH_DIRECTION_RE = re.compile(
    r"<d>|\{[^{}]+\}|\b(?:says?|asks?|repl(?:y|ies)|speaks?|shouts?|"
    r"whispers?|dialogue|voice[ -]?over)\b|对白|说[：:]",
    re.IGNORECASE,
)
_SILENCE_RE = re.compile(
    r"全程(?:静音|无声)|完全(?:静音|无声)|无声视频|complete silence|"
    r"entirely silent|silent video",
    re.IGNORECASE,
)
_EXPLICIT_SOUND_RE = re.compile(
    r"音效|环境音|环境声|脚步|碰撞|呼吸|笑声|sound(?:scape| effect)?|"
    r"ambience|ambient|footsteps?|impact|breath",
    re.IGNORECASE,
)
_MUSIC_LINE_RE = re.compile(
    r"^(?:画外配乐|背景配乐|配乐|BGM|background score|"
    r"non[-_ ]diegetic music)[：:]\s*(?P<value>.+)$",
    re.IGNORECASE,
)
_AUDIO_POLICY_MARKERS = (
    "Every audible spoken word",
    "The soundtrack consists exclusively",
)


@dataclass(frozen=True)
class _H3Params:
    width: int
    height: int
    seconds: float
    steps: int
    seed: int | None


@dataclass(frozen=True)
class _ReferenceSpec:
    uri: str
    asset_type: str | None = None
    reference_role: str | None = None
    entity_type: str | None = None
    entity_id: str | None = None


@dataclass(frozen=True)
class _ImageInput:
    filename: str
    content: bytes
    mime_type: str
    reference: _ReferenceSpec


class MiniMaxH3GatewayProvider(ProviderAdapter):
    name = "minimax_h3_gateway"
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
        "automatic_model_routing",
        "minimax_h3",
    ]
    native_audio = True
    reference_images = True
    multi_reference = True
    min_duration_sec = 0.2
    max_duration_sec = 15
    supported_resolutions = ["1024x576", "576x1024"]

    def __init__(self) -> None:
        self.model = _configured_model()
        self.base_url = str(settings.minimax_h3_gateway_base_url or "").rstrip("/")
        self.api_key = settings.minimax_h3_gateway_api_key

    def validate_config(self) -> None:
        if not self.base_url:
            raise ValueError(
                "MINIMAX_H3_GATEWAY_BASE_URL is required for MiniMax H3 gateway"
            )
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("MiniMax H3 gateway Base URL must be HTTP(S)")
        if not self.base_url.endswith("/v1"):
            raise ValueError("MiniMax H3 gateway Base URL must include the /v1 path")
        _validate_requested_model(self.model)

    def submit(self, request: ProviderRequest) -> ProviderResponse:
        provider_task_id = str(
            uuid5(
                NAMESPACE_URL,
                f"{request.project_id}:{request.task_id}:minimax-h3",
            )
        )
        submission_state = SubmissionState.NOT_SUBMITTED
        try:
            self.validate_config()
            params = _generation_params(request)
            reference_specs = _reference_specs(request)
            resolved_model = _resolve_model(request, reference_specs, self.model)
            selected_specs = _select_reference_specs(
                reference_specs,
                resolved_model=resolved_model,
            )
            image_inputs = [_load_image(item, request.task_id) for item in selected_specs]
            prompt = _provider_prompt(
                request,
                selected_specs,
                resolved_model=resolved_model,
            )
            fields: dict[str, object] = {
                "model": resolved_model,
                "prompt": prompt,
                "size": f"{params.width}x{params.height}",
                "seconds": _compact_number(params.seconds),
                "steps": params.steps,
            }
            if params.seed is not None:
                fields["seed"] = params.seed
            body, content_type = _multipart_body(fields, image_inputs)
            submission_state = SubmissionState.UNKNOWN
            submitted = self._request_json(
                f"{self.base_url}/videos",
                method="POST",
                data=body,
                headers={"Content-Type": content_type},
            )
            provider_task_id = _provider_task_id(submitted)
            submission_state = SubmissionState.ACCEPTED
            status = _provider_status(submitted)
            error = _terminal_error(submitted, status=status)
            return ProviderResponse(
                status=status,
                provider_task_id=provider_task_id,
                execution_mode=ProviderExecutionMode.ASYNC,
                poll_after_sec=(
                    settings.minimax_h3_gateway_poll_interval_sec
                    if status in {ProviderStatus.QUEUED, ProviderStatus.RUNNING}
                    else None
                ),
                raw_response={
                    "gateway_response": submitted,
                    "request_contract": {
                        "requested_model": request.model or self.model,
                        "resolved_model": resolved_model,
                        "size": f"{params.width}x{params.height}",
                        "seconds": params.seconds,
                        "steps": params.steps,
                        "reference_count": len(image_inputs),
                        "reference_roles": [
                            item.reference.reference_role for item in image_inputs
                        ],
                        "prompt_contract_version": H3_AUDIO_PROMPT_VERSION,
                    },
                },
                usage=ProviderUsage(cost=Decimal("0"), unit="LOCAL_GPU"),
                error=error,
            )
        except (
            HTTPError,
            URLError,
            TimeoutError,
            OSError,
            ValueError,
            json.JSONDecodeError,
            binascii.Error,
        ) as exc:
            message, raw_error, code, retryable = _exception_details(exc)
            if isinstance(exc, HTTPError) and 400 <= exc.code < 500:
                submission_state = SubmissionState.NOT_SUBMITTED
            return _failed_response(
                provider_task_id,
                code,
                message,
                raw_response=raw_error,
                retryable=retryable,
                submission_state=submission_state,
            )

    def poll(self, provider_task_id: str) -> ProviderResponse:
        self.validate_config()
        payload = self._request_json(
            f"{self.base_url}/videos/{provider_task_id}",
            method="GET",
        )
        return self.normalize_response(payload)

    def fetch_result(
        self,
        provider_task_id: str,
        *,
        request: ProviderRequest | None = None,
        provider_context: dict | None = None,
    ) -> ProviderResponse:
        _ = provider_context
        if request is None:
            raise ValueError("MiniMax H3 fetch_result requires the original request snapshot")
        polled = self.poll(provider_task_id)
        if polled.status != ProviderStatus.SUCCEEDED:
            return polled
        params = _generation_params(request)
        output_path, mime_type = self._download_result(provider_task_id)
        request_specs = _reference_specs(request)
        resolved_model = _resolve_model(request, request_specs, self.model)
        return ProviderResponse(
            status=ProviderStatus.SUCCEEDED,
            provider_task_id=provider_task_id,
            execution_mode=ProviderExecutionMode.ASYNC,
            assets=[
                ProviderAsset(
                    asset_type="video",
                    uri=str(output_path),
                    mime_type=mime_type,
                    width=params.width,
                    height=params.height,
                    duration_sec=Decimal(str(params.seconds)).quantize(Decimal("0.001")),
                    metadata={
                        "provider_task_id": provider_task_id,
                        "resolved_model": resolved_model,
                        "temporary_file": True,
                        "native_audio": True,
                    },
                )
            ],
            raw_response=polled.raw_response,
            usage=ProviderUsage(cost=Decimal("0"), unit="LOCAL_GPU"),
        )

    def cancel(self, provider_task_id: str) -> None:
        self.validate_config()
        self._request_json(
            f"{self.base_url}/videos/{provider_task_id}/cancel",
            method="POST",
            data=b"",
        )

    def normalize_response(self, raw_response: dict) -> ProviderResponse:
        provider_task_id = _provider_task_id(raw_response)
        status = _provider_status(raw_response)
        return ProviderResponse(
            status=status,
            provider_task_id=provider_task_id,
            execution_mode=ProviderExecutionMode.ASYNC,
            poll_after_sec=(
                settings.minimax_h3_gateway_poll_interval_sec
                if status in {ProviderStatus.QUEUED, ProviderStatus.RUNNING}
                else None
            ),
            raw_response=raw_response,
            error=_terminal_error(raw_response, status=status),
        )

    def _request_json(
        self,
        url: str,
        *,
        method: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict:
        request = Request(
            url,
            data=data,
            headers={**self._auth_headers(), **(headers or {})},
            method=method,
        )
        with urlopen(
            request,
            timeout=settings.minimax_h3_gateway_timeout_sec,
        ) as response:
            body = response.read()
        payload = json.loads(body.decode("utf-8")) if body else {}
        if not isinstance(payload, dict):
            raise ValueError("MiniMax H3 gateway returned a non-object response")
        return payload

    def _download_result(self, provider_task_id: str) -> tuple[Path, str]:
        request = Request(
            f"{self.base_url}/videos/{provider_task_id}/content",
            headers=self._auth_headers(),
            method="GET",
        )
        output_path: Path | None = None
        try:
            with urlopen(request, timeout=settings.provider_timeout_sec) as response:
                mime_type = response.headers.get("Content-Type", "video/mp4")
                mime_type = mime_type.split(";", 1)[0].strip().lower()
                if mime_type in {"", "application/octet-stream"}:
                    mime_type = "video/mp4"
                if not mime_type.startswith("video/"):
                    raise ValueError(
                        f"MiniMax H3 content endpoint returned {mime_type or 'unknown data'}"
                    )
                suffix = mimetypes.guess_extension(mime_type) or ".mp4"
                with tempfile.NamedTemporaryFile(
                    prefix=f"verbascene-minimax-h3-{provider_task_id}-",
                    suffix=suffix,
                    delete=False,
                ) as output:
                    output_path = Path(output.name)
                    while chunk := response.read(1024 * 1024):
                        output.write(chunk)
            return output_path, mime_type
        except Exception:
            if output_path is not None:
                output_path.unlink(missing_ok=True)
            raise

    def _auth_headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        return {"Authorization": f"Bearer {self.api_key}"}


def _configured_model() -> str:
    if (
        settings.video_provider == MiniMaxH3GatewayProvider.name
        and settings.video_model not in {"", "mock-video"}
    ):
        return settings.video_model
    return settings.minimax_h3_gateway_model


def _generation_params(request: ProviderRequest) -> _H3Params:
    width, height = _video_dimensions(request)
    seconds = float(
        request.params.get(
            "seconds",
            request.params.get("duration_sec", request.params.get("duration", 5)),
        )
    )
    steps = int(request.params.get("steps", settings.minimax_h3_gateway_steps))
    seed_value = request.params.get("seed", settings.minimax_h3_gateway_seed)
    seed = None if seed_value is None or str(seed_value).strip() == "" else int(seed_value)
    if (
        width < 32
        or height < 32
        or width > 2048
        or height > 2048
        or width % 32
        or height % 32
        or width * height > 2_100_000
    ):
        raise ValueError(
            "MiniMax H3 dimensions must be 32-2048, divisible by 32, and at most 2100000 pixels"
        )
    if not 0.2 <= seconds <= 15:
        raise ValueError("MiniMax H3 duration must be between 0.2 and 15 seconds")
    if not 1 <= steps <= 100:
        raise ValueError("MiniMax H3 steps must be between 1 and 100")
    if seed is not None and not 0 <= seed <= 2**64 - 1:
        raise ValueError("MiniMax H3 seed must be an unsigned 64-bit integer")
    return _H3Params(
        width=width,
        height=height,
        seconds=seconds,
        steps=steps,
        seed=seed,
    )


def _video_dimensions(request: ProviderRequest) -> tuple[int, int]:
    width_value = request.params.get("width")
    height_value = request.params.get("height")
    if width_value is not None or height_value is not None:
        if width_value is None or height_value is None:
            raise ValueError("MiniMax H3 width and height must be provided together")
        return int(width_value), int(height_value)
    size = request.params.get("size")
    if not size:
        ratio = str(request.params.get("ratio") or "16:9")
        size = (
            settings.minimax_h3_gateway_portrait_size
            if ratio == "9:16"
            else settings.minimax_h3_gateway_landscape_size
        )
    normalized = str(size).strip().lower().replace("*", "x")
    if "x" not in normalized:
        raise ValueError("MiniMax H3 size must use WIDTHxHEIGHT")
    width_text, height_text = normalized.split("x", 1)
    return int(width_text), int(height_text)


def _validate_requested_model(model: str | None) -> None:
    normalized = str(model or "").strip().lower()
    if normalized not in {*AUTO_MODELS, R2V_MODEL, FL2VA_MODEL}:
        raise ValueError(
            "MiniMax H3 model must be auto, minimax-h3-r2v, or minimax-h3-fl2va"
        )


def _resolve_model(
    request: ProviderRequest,
    references: list[_ReferenceSpec],
    default_model: str,
) -> str:
    requested = str(request.model or default_model or "auto").strip().lower()
    _validate_requested_model(requested)
    if requested == R2V_MODEL:
        if not references:
            raise ValueError("minimax-h3-r2v requires at least one reference image")
        return R2V_MODEL
    if requested == FL2VA_MODEL:
        return FL2VA_MODEL
    if any(item.reference_role == "video_first_frame" for item in references):
        return FL2VA_MODEL
    return R2V_MODEL if references else FL2VA_MODEL


def _reference_specs(request: ProviderRequest) -> list[_ReferenceSpec]:
    asset_resolution = request.metadata.get("asset_resolution")
    raw_assets = (
        asset_resolution.get("reference_assets")
        if isinstance(asset_resolution, dict)
        else None
    )
    metadata_assets = [item for item in raw_assets or [] if isinstance(item, dict)]
    unused = list(metadata_assets)
    result: list[_ReferenceSpec] = []
    for uri in request.references:
        match_index = next(
            (
                index
                for index, item in enumerate(unused)
                if str(item.get("uri") or "") == uri
            ),
            None,
        )
        metadata = unused.pop(match_index) if match_index is not None else {}
        result.append(
            _ReferenceSpec(
                uri=uri,
                asset_type=str(metadata.get("asset_type") or "") or None,
                reference_role=str(metadata.get("reference_role") or "") or None,
                entity_type=str(metadata.get("entity_type") or "") or None,
                entity_id=str(metadata.get("entity_id") or "") or None,
            )
        )
    return result


def _select_reference_specs(
    references: list[_ReferenceSpec],
    *,
    resolved_model: str,
) -> list[_ReferenceSpec]:
    if resolved_model == FL2VA_MODEL:
        first_frame = next(
            (
                item
                for item in references
                if item.reference_role == "video_first_frame"
            ),
            None,
        )
        selected = [first_frame] if first_frame is not None else references[:2]
    else:
        selected = references
    if len(selected) > MAX_REFERENCE_IMAGES:
        raise ValueError(
            f"MiniMax H3 supports at most {MAX_REFERENCE_IMAGES} reference images"
        )
    for item in selected:
        if item.asset_type and item.asset_type != "image":
            raise ValueError(
                "The simplified MiniMax H3 gateway adapter currently accepts image references only"
            )
    return selected


def _load_image(reference: _ReferenceSpec, task_id: str) -> _ImageInput:
    uri = reference.uri
    if uri.startswith("data:"):
        try:
            header, encoded = uri.split(",", 1)
            mime_type = header.split(":", 1)[1].split(";", 1)[0].lower()
            if ";base64" not in header.lower() or not mime_type.startswith("image/"):
                raise ValueError("Reference Data URL must contain a base64 image")
            content = base64.b64decode(encoded, validate=True)
        except (IndexError, ValueError, binascii.Error) as exc:
            raise ValueError("Invalid MiniMax H3 image Data URL") from exc
        _assert_image_size(content)
        return _ImageInput(
            filename=_safe_filename(
                f"{task_id}{mimetypes.guess_extension(mime_type) or '.png'}"
            ),
            content=content,
            mime_type=mime_type,
            reference=reference,
        )

    try:
        local_path = get_media_store().resolve_local_path(uri)
    except (FileNotFoundError, ValueError):
        local_path = None
    if local_path is not None:
        if local_path.stat().st_size > MAX_IMAGE_BYTES:
            raise ValueError("MiniMax H3 reference image exceeds 50 MB")
        mime_type = mimetypes.guess_type(local_path.name)[0] or "image/png"
        if not mime_type.startswith("image/"):
            raise ValueError("MiniMax H3 reference file must be an image")
        return _ImageInput(
            filename=_safe_filename(local_path.name),
            content=local_path.read_bytes(),
            mime_type=mime_type,
            reference=reference,
        )

    if uri.startswith(("http://", "https://")):
        with urlopen(
            Request(uri, headers={"User-Agent": "VerbaScene/0.1"}, method="GET"),
            timeout=settings.provider_timeout_sec,
        ) as response:
            content = response.read(MAX_IMAGE_BYTES + 1)
            mime_type = (
                response.headers.get("Content-Type")
                or mimetypes.guess_type(urlparse(uri).path)[0]
                or "image/png"
            ).split(";", 1)[0].strip().lower()
        _assert_image_size(content)
        if not mime_type.startswith("image/"):
            raise ValueError("MiniMax H3 reference URL must return an image")
        return _ImageInput(
            filename=_safe_filename(
                Path(urlparse(uri).path).name
                or f"{task_id}{mimetypes.guess_extension(mime_type) or '.png'}"
            ),
            content=content,
            mime_type=mime_type,
            reference=reference,
        )
    raise ValueError(f"Unsupported MiniMax H3 reference URI: {uri[:48]}")


def _assert_image_size(content: bytes) -> None:
    if not content:
        raise ValueError("MiniMax H3 reference image is empty")
    if len(content) > MAX_IMAGE_BYTES:
        raise ValueError("MiniMax H3 reference image exceeds 50 MB")


def _provider_prompt(
    request: ProviderRequest,
    references: list[_ReferenceSpec],
    *,
    resolved_model: str,
) -> str:
    lines = [
        line
        for line in request.prompt.splitlines()
        if not line.strip().lower().startswith(("参考绑定：", "reference binding:"))
    ]
    prompt = "\n".join(lines).strip()
    if request.negative_prompt:
        prompt = f"{prompt}\n负面约束：{request.negative_prompt.strip()}".strip()
    prompt = _h3_audio_prompt(prompt)
    if references:
        if resolved_model == FL2VA_MODEL:
            binding = (
                "首帧约束：将 <Picture 1> 严格作为视频首帧和构图起点，"
                "保持主体身份、服装、场景布局和光线连续。"
            )
        else:
            binding = "参考绑定：" + "；".join(
                f"<Picture {index}>={_reference_label(item, request)}"
                for index, item in enumerate(references, start=1)
            ) + "。保持各参考图对应的身份、外观、材质与空间关系。"
        prompt = f"{binding}\n{prompt}" if prompt else binding
    if not prompt:
        raise ValueError("MiniMax H3 prompt cannot be empty")
    if len(prompt) > 12_000:
        raise ValueError("MiniMax H3 prompt cannot exceed 12000 characters")
    return prompt


def _h3_audio_prompt(prompt: str) -> str:
    """Add H3's native-audio structure without mutating the stored shot prompt."""

    if _has_ordered_fields(prompt, _H3_CORE_FIELDS) or _has_ordered_fields(
        prompt,
        _H3_FULL_REFERENCE_FIELDS,
    ):
        return _ensure_audio_exclusivity(prompt)

    integrated, dialogue_found, music = _normalize_h3_integrated_description(prompt)
    if not integrated:
        raise ValueError("MiniMax H3 prompt cannot be empty")
    policy = _audio_exclusivity_policy(
        tagged_dialogue=dialogue_found,
        speech_requested=bool(_SPEECH_DIRECTION_RE.search(integrated)),
    )
    integrated = f"{integrated.rstrip()} {policy}".strip()

    if _SILENCE_RE.search(prompt):
        soundscape = "N/A"
    elif _EXPLICIT_SOUND_RE.search(prompt):
        soundscape = (
            "A clean, restrained mix carries the environmental and physical action "
            "sounds explicitly synchronized in the shot description. Each sound is "
            "caused by its visible action and remains beneath the dialogue."
        )
    else:
        soundscape = (
            "A clean, low-level room tone supports the scene, with restrained physical "
            "sounds caused by visible actions."
        )

    return (
        f"integrated_multimodal_description: [Shot 1] {integrated}\n\n"
        f"overall_soundscape: {soundscape}\n\n"
        f"non_diegetic_music: {music or 'N/A'}"
    )


def _has_ordered_fields(prompt: str, fields: tuple[str, ...]) -> bool:
    position = -1
    for field in fields:
        match = re.search(rf"(?im)^\s*{re.escape(field)}\s*:", prompt)
        if match is None or match.start() <= position:
            return False
        position = match.start()
    return True


def _ensure_audio_exclusivity(prompt: str) -> str:
    folded_prompt = prompt.casefold()
    if any(marker.casefold() in folded_prompt for marker in _AUDIO_POLICY_MARKERS):
        return prompt.strip()
    soundscape = re.search(r"(?im)^\s*overall_soundscape\s*:", prompt)
    if soundscape is None:
        return prompt.strip()
    policy = _audio_exclusivity_policy(
        tagged_dialogue="<d>" in prompt,
        speech_requested=bool(_SPEECH_DIRECTION_RE.search(prompt[: soundscape.start()])),
    )
    return (
        f"{prompt[:soundscape.start()].rstrip()} {policy}\n\n"
        f"{prompt[soundscape.start():].lstrip()}"
    )


def _normalize_h3_integrated_description(prompt: str) -> tuple[str, bool, str | None]:
    lines: list[str] = []
    speaker_ids: dict[str, str] = {}
    dialogue_found = False
    music: str | None = None

    for raw_line in prompt.splitlines():
        line = raw_line.strip()
        if not line or line == _GENERIC_AUDIO_INTRO:
            continue
        music_match = _MUSIC_LINE_RE.match(line)
        if music_match:
            music = music_match.group("value").strip()
            continue
        if line.startswith("对白："):
            rendered, found = _h3_dialogue_line(line.removeprefix("对白："), speaker_ids)
            lines.append(rendered if rendered else line)
            dialogue_found = dialogue_found or found
            continue
        if line.startswith("音效："):
            sound_cues = (
                line.removeprefix("音效：")
                .rstrip("。")
                .replace("<", "")
                .replace(">", "")
            )
            lines.append(
                "Synchronized diegetic sound: "
                + sound_cues
                + "."
            )
            continue
        lines.append(line)

    return " ".join(lines), dialogue_found, music


def _h3_dialogue_line(
    value: str,
    speaker_ids: dict[str, str],
) -> tuple[str, bool]:
    rendered: list[str] = []
    found = False
    for segment in (item.strip() for item in value.split("；")):
        if not segment:
            continue
        match = _DIALOGUE_WITH_EMOTION_RE.match(segment)
        if match is None:
            match = _DIALOGUE_RE.match(segment)
        if match is None:
            rendered.append(f"对白：{segment}")
            continue

        speaker = match.group("speaker").strip()
        text = match.group("text").strip()
        if not speaker or not text:
            rendered.append(f"对白：{segment}")
            continue
        key = speaker.casefold()
        speaker_id = speaker_ids.setdefault(key, f"S{len(speaker_ids) + 1}")
        emotion = (
            match.groupdict().get("emotion", "").strip()
            if match.re is _DIALOGUE_WITH_EMOTION_RE
            else ""
        )
        if _VOICEOVER_RE.search(speaker):
            rendered.append(
                f"A stable off-screen voice named {speaker} ({speaker_id}) says in an "
                f"off-screen voiceover: <d>[English] {text}</d>."
            )
        else:
            delivery = f" with a {emotion} delivery" if emotion else ""
            rendered.append(
                f"{speaker} ({speaker_id}) says{delivery}: "
                f"<d>[English] {text}</d>."
            )
        found = True
    return " ".join(rendered), found


def _audio_exclusivity_policy(
    *,
    tagged_dialogue: bool,
    speech_requested: bool,
) -> str:
    if tagged_dialogue:
        return (
            "Every audible spoken word comes from exactly one scripted <d> block and is "
            "voiced once by its assigned (Sx) speaker. Only those assigned speakers "
            "produce speech; every other character says no words and keeps a naturally "
            "closed mouth except during an explicitly scripted non-verbal reaction."
        )
    if speech_requested:
        return (
            "Every audible spoken word comes from the explicitly scripted speech and is "
            "voiced once by its assigned speaker. Only those speakers produce speech; "
            "every other character says no words and keeps a naturally closed mouth except "
            "during an explicitly scripted non-verbal reaction."
        )
    return (
        "The soundtrack consists exclusively of non-verbal ambience and synchronized "
        "physical sounds caused by visible actions; no character speaks, and mouths remain "
        "naturally closed except during an explicitly scripted non-verbal reaction."
    )


def _reference_label(reference: _ReferenceSpec, request: ProviderRequest) -> str:
    asset_resolution = request.metadata.get("asset_resolution")
    prompt_context = (
        asset_resolution.get("prompt_context")
        if isinstance(asset_resolution, dict)
        and isinstance(asset_resolution.get("prompt_context"), dict)
        else {}
    )
    entity_id = reference.entity_id
    if reference.entity_type == "character" and entity_id:
        for item in prompt_context.get("characters") or []:
            if isinstance(item, dict) and str(item.get("id") or "") == entity_id:
                return f"角色“{item.get('name') or '角色'}”身份参考"
    if reference.entity_type == "scene" and entity_id:
        scene = prompt_context.get("scene")
        if isinstance(scene, dict) and str(scene.get("id") or "") == entity_id:
            return f"场景“{scene.get('name') or '当前场景'}”空间参考"
    if reference.entity_type == "prop" and entity_id:
        for item in prompt_context.get("props") or []:
            if isinstance(item, dict) and str(item.get("id") or "") == entity_id:
                return f"道具“{item.get('name') or '道具'}”外观参考"
    return {
        "video_first_frame": "当前片段构图参考",
        "character_main_ref": "角色身份参考",
        "scene_ref": "场景空间参考",
        "prop_ref": "道具外观参考",
    }.get(reference.reference_role or "", "视觉参考")


def _multipart_body(
    fields: dict[str, object],
    images: list[_ImageInput],
) -> tuple[bytes, str]:
    boundary = f"----verbascene-minimax-h3-{uuid4().hex}"
    parts: list[bytes] = []
    for key, value in fields.items():
        parts.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode(),
                str(value).encode("utf-8"),
                b"\r\n",
            ]
        )
    for image in images:
        parts.extend(
            [
                f"--{boundary}\r\n".encode(),
                (
                    'Content-Disposition: form-data; name="input_reference"; '
                    f'filename="{image.filename}"\r\n'
                    f"Content-Type: {image.mime_type}\r\n\r\n"
                ).encode("utf-8"),
                image.content,
                b"\r\n",
            ]
        )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _safe_filename(value: str) -> str:
    name = Path(value).name
    sanitized = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return sanitized[:180] or "reference.png"


def _provider_task_id(payload: dict) -> str:
    value = payload.get("id") or payload.get("video_id") or payload.get("task_id")
    if not value:
        raise ValueError("MiniMax H3 gateway response did not include a video id")
    return str(value)


def _provider_status(payload: dict) -> ProviderStatus:
    value = str(payload.get("status") or "").strip().lower()
    mapping = {
        "queued": ProviderStatus.QUEUED,
        "pending": ProviderStatus.QUEUED,
        "running": ProviderStatus.RUNNING,
        "processing": ProviderStatus.RUNNING,
        "completed": ProviderStatus.SUCCEEDED,
        "succeeded": ProviderStatus.SUCCEEDED,
        "failed": ProviderStatus.FAILED,
        "cancelled": ProviderStatus.CANCELLED,
        "canceled": ProviderStatus.CANCELLED,
    }
    if value not in mapping:
        raise ValueError(f"Unknown MiniMax H3 gateway status: {value or '<empty>'}")
    return mapping[value]


def _terminal_error(payload: dict, *, status: ProviderStatus) -> ProviderError | None:
    if status != ProviderStatus.FAILED:
        return None
    raw_error = payload.get("error")
    message = (
        raw_error.get("message")
        if isinstance(raw_error, dict)
        else raw_error
    ) or payload.get("message") or payload.get("detail") or "MiniMax H3 task failed"
    return ProviderError(
        error_code="minimax_h3_job_failed",
        error_message=str(message),
        is_retryable=False,
        submission_state=SubmissionState.ACCEPTED,
        raw_error=payload,
    )


def _failed_response(
    provider_task_id: str,
    code: str,
    message: str,
    *,
    raw_response: dict,
    retryable: bool,
    submission_state: SubmissionState,
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


def _exception_details(exc: Exception) -> tuple[str, dict, str, bool]:
    if isinstance(exc, HTTPError):
        raw_text = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw_text) if raw_text else {}
            raw = parsed if isinstance(parsed, dict) else {"response": parsed}
        except json.JSONDecodeError:
            raw = {"raw_text": raw_text}
        raw["status_code"] = exc.code
        message = raw.get("detail") or raw.get("message") or raw.get("error") or str(exc)
        if isinstance(message, dict):
            message = message.get("message") or message.get("detail") or str(message)
        return (
            str(message),
            raw,
            f"minimax_h3_http_{exc.code}",
            exc.code in {408, 425, 429} or exc.code >= 500,
        )
    return (
        str(exc) or exc.__class__.__name__,
        {},
        "minimax_h3_gateway_failed",
        isinstance(exc, (URLError, TimeoutError, OSError)),
    )


def _compact_number(value: float) -> str:
    return f"{value:g}"


def build_minimax_h3_providers() -> list[ProviderAdapter]:
    return [MiniMaxH3GatewayProvider()]
