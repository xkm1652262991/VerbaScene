"""OpenAI-compatible speech recognition used by subtitle alignment."""

from __future__ import annotations

import json
import mimetypes
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from app.core.config import settings


@dataclass(frozen=True)
class ASRTimedToken:
    text: str
    start_sec: Decimal
    end_sec: Decimal


@dataclass(frozen=True)
class ASRTranscript:
    text: str
    language: str | None
    tokens: tuple[ASRTimedToken, ...]
    response_format: str


class ASRProviderError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.status_code = status_code


class OpenAICompatibleASRAdapter:
    name = "openai_compatible"

    def __init__(self) -> None:
        self.model = settings.asr_model
        self.api_key = settings.asr_api_key
        self.base_url = (settings.asr_base_url or "").rstrip("/")
        self.timeout_sec = max(1, int(settings.asr_timeout_sec))
        self.response_format = settings.asr_response_format
        self._verbose_supported: bool | None = (
            False if self.response_format == "json" else None
        )

    def validate_config(self) -> None:
        if not self.base_url:
            raise ASRProviderError("asr_base_url_missing", "ASR_BASE_URL is required")
        if not self.api_key:
            raise ASRProviderError("asr_api_key_missing", "ASR_API_KEY is required")
        if not self.model:
            raise ASRProviderError("asr_model_missing", "ASR_MODEL is required")

    def transcribe(
        self,
        audio_path: Path,
        *,
        duration_sec: Decimal | None = None,
    ) -> ASRTranscript:
        self.validate_config()
        source = audio_path.expanduser().resolve()
        if not source.is_file():
            raise ASRProviderError(
                "asr_audio_missing",
                f"ASR audio input does not exist: {source}",
            )

        if self.response_format == "verbose_json":
            raw = self._request(source, response_format="verbose_json")
            return _normalize_transcript(
                raw,
                response_format="verbose_json",
                duration_sec=duration_sec,
            )

        if self._verbose_supported is not False:
            try:
                raw = self._request(source, response_format="verbose_json")
                self._verbose_supported = True
                return _normalize_transcript(
                    raw,
                    response_format="verbose_json",
                    duration_sec=duration_sec,
                )
            except ASRProviderError as exc:
                if not _verbose_json_is_unsupported(exc):
                    raise
                self._verbose_supported = False

        raw = self._request(source, response_format="json")
        return _normalize_transcript(
            raw,
            response_format="json",
            duration_sec=duration_sec,
        )

    def _request(self, audio_path: Path, *, response_format: str) -> dict[str, Any]:
        fields: list[tuple[str, str]] = [
            ("model", self.model),
            ("response_format", response_format),
        ]
        if response_format == "verbose_json":
            fields.extend(
                [
                    ("timestamp_granularities[]", "word"),
                    ("timestamp_granularities[]", "segment"),
                ]
            )
        body, content_type = _encode_multipart(fields, audio_path)
        request = Request(
            f"{self.base_url}/audio/transcriptions",
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": content_type,
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_sec) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raw_body = exc.read().decode("utf-8", errors="replace")
            code, message = _http_error_detail(exc.code, raw_body)
            raise ASRProviderError(
                code,
                message,
                retryable=exc.code == 429 or exc.code >= 500,
                status_code=exc.code,
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ASRProviderError(
                "asr_transport_failed",
                str(exc) or exc.__class__.__name__,
                retryable=True,
            ) from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ASRProviderError(
                "asr_response_invalid",
                "ASR response is not valid JSON",
            ) from exc
        if not isinstance(payload, dict):
            raise ASRProviderError(
                "asr_response_invalid",
                "ASR response must be a JSON object",
            )
        return payload


def build_asr_adapter() -> OpenAICompatibleASRAdapter | None:
    if settings.asr_provider == "disabled":
        return None
    if settings.asr_provider == "openai_compatible":
        return OpenAICompatibleASRAdapter()
    return None


def _normalize_transcript(
    raw: dict[str, Any],
    *,
    response_format: str,
    duration_sec: Decimal | None,
) -> ASRTranscript:
    payload = _transcript_payload(raw)
    text = _first_text(payload, "text", "transcript", "transcription")
    language = _first_text(payload, "language", "detected_language") or None
    timed_items: list[dict[str, Any]] = []
    for key in ("words", "time_stamps", "timestamps", "chunks", "segments"):
        value = payload.get(key)
        if isinstance(value, list):
            timed_items = [item for item in value if isinstance(item, dict)]
            if timed_items:
                break
    tokens = _tokens_from_timed_items(timed_items, duration_sec=duration_sec)
    return ASRTranscript(
        text=text,
        language=language,
        tokens=tuple(tokens),
        response_format=response_format,
    )


def _transcript_payload(raw: dict[str, Any]) -> dict[str, Any]:
    for key in ("output", "result", "data", "transcription"):
        nested = raw.get(key)
        if isinstance(nested, dict):
            return nested
    return raw


def _tokens_from_timed_items(
    items: list[dict[str, Any]],
    *,
    duration_sec: Decimal | None,
) -> list[ASRTimedToken]:
    parsed: list[tuple[str, Decimal, Decimal]] = []
    for item in items:
        text = _first_text(item, "word", "text", "content").strip()
        start_value, end_value = _timestamp_values(item)
        start = _decimal_or_none(start_value)
        end = _decimal_or_none(end_value)
        if not text or start is None or end is None or end <= start:
            continue
        parsed.append((text, start, end))
    if not parsed:
        return []

    scale = Decimal("1")
    if duration_sec is not None and duration_sec > 0:
        latest_end = max(item[2] for item in parsed)
        if latest_end > max(Decimal("100"), duration_sec * Decimal("10")):
            scale = Decimal("1000")

    tokens: list[ASRTimedToken] = []
    for text, raw_start, raw_end in parsed:
        start = raw_start / scale
        end = raw_end / scale
        pieces = _tokenize_display_text(text)
        if not pieces:
            continue
        piece_duration = (end - start) / Decimal(len(pieces))
        for index, piece in enumerate(pieces):
            token_start = start + piece_duration * index
            token_end = start + piece_duration * (index + 1)
            tokens.append(
                ASRTimedToken(
                    text=piece,
                    start_sec=token_start,
                    end_sec=token_end,
                )
            )
    return sorted(tokens, key=lambda item: (item.start_sec, item.end_sec))


def _timestamp_values(item: dict[str, Any]) -> tuple[Any, Any]:
    start = item.get("start")
    if start is None:
        start = item.get("start_time")
    end = item.get("end")
    if end is None:
        end = item.get("end_time")
    timestamp = item.get("timestamp")
    if isinstance(timestamp, (list, tuple)) and len(timestamp) >= 2:
        start = timestamp[0] if start is None else start
        end = timestamp[1] if end is None else end
    return start, end


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _first_text(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return ""


def _tokenize_display_text(text: str) -> list[str]:
    return [part for part in text.replace("\n", " ").split() if part]


def _verbose_json_is_unsupported(exc: ASRProviderError) -> bool:
    if exc.status_code != 400:
        return False
    message = str(exc).lower()
    return "verbose_json" in message and (
        "not support" in message or "unsupported" in message
    )


def _http_error_detail(status_code: int, raw_body: str) -> tuple[str, str]:
    code = f"asr_http_{status_code}"
    message = raw_body.strip() or f"ASR request failed with HTTP {status_code}"
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        return code, message[:500]
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        provider_code = error.get("code") or error.get("type")
        provider_message = error.get("message")
        if provider_code:
            code = str(provider_code)
        if provider_message:
            message = str(provider_message)
    return code[:120], message[:500]


def _encode_multipart(
    fields: list[tuple[str, str]],
    audio_path: Path,
) -> tuple[bytes, str]:
    boundary = f"----verbascene-asr-{uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields:
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("ascii"),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(
                    "utf-8"
                ),
                value.encode("utf-8"),
                b"\r\n",
            ]
        )
    filename = audio_path.name.replace('"', "_")
    mime_type = mimetypes.guess_type(filename)[0] or "audio/wav"
    chunks.extend(
        [
            f"--{boundary}\r\n".encode("ascii"),
            (
                f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            ).encode("utf-8"),
            f"Content-Type: {mime_type}\r\n\r\n".encode("ascii"),
            audio_path.read_bytes(),
            b"\r\n",
            f"--{boundary}--\r\n".encode("ascii"),
        ]
    )
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"
