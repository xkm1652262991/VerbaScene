"""Align frozen project dialogue to speech in selected video clips."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Protocol

from app.core.config import settings
from app.platform.media import get_media_store
from app.platform.media.subprocess_runner import (
    MediaProcessCancelled,
    MediaProcessResult,
    run_cancellable_media_process,
)
from app.providers.openai_compatible_asr import (
    ASRProviderError,
    ASRTimedToken,
    ASRTranscript,
    build_asr_adapter,
)


_SILENCE_EVENT_RE = re.compile(
    r"silence_(?P<event>start|end):\s*(?P<time>\d+(?:\.\d+)?)"
)
_MATCH_TOKEN_RE = re.compile(r"[a-z0-9]+(?:['’][a-z0-9]+)*", re.IGNORECASE)
_VOICE_REGION_MIN_SEC = Decimal("0.25")
_VOICE_REGION_PAD_SEC = Decimal("0.12")
_CUE_LEAD_PAD_SEC = Decimal("0.10")
_CUE_TRAIL_PAD_SEC = Decimal("0.20")
_CUE_SEPARATION_SEC = Decimal("0.04")
_MAX_VOICE_REGIONS = 16


class ASRTranscriber(Protocol):
    name: str
    model: str

    def transcribe(
        self,
        audio_path: Path,
        *,
        duration_sec: Decimal | None = None,
    ) -> ASRTranscript: ...


@dataclass(frozen=True)
class AlignedDialogueTiming:
    dialogue_id: str
    start_sec: Decimal
    end_sec: Decimal
    confidence: float


@dataclass(frozen=True)
class SubtitleAlignmentResult:
    status: str
    provider: str | None
    model: str | None
    timings: dict[str, AlignedDialogueTiming]
    target_count: int
    attempted_clip_count: int
    native_timestamp_clip_count: int
    vad_clip_count: int
    issues: tuple[dict[str, str], ...] = ()

    @property
    def aligned_count(self) -> int:
        return len(self.timings)

    @property
    def fallback_count(self) -> int:
        return max(0, self.target_count - self.aligned_count)

    def to_manifest(self) -> dict[str, Any]:
        return {
            "policy_version": "asr-vad-v1",
            "status": self.status,
            "provider": self.provider,
            "model": self.model,
            "target_count": self.target_count,
            "aligned_count": self.aligned_count,
            "fallback_count": self.fallback_count,
            "attempted_clip_count": self.attempted_clip_count,
            "native_timestamp_clip_count": self.native_timestamp_clip_count,
            "vad_clip_count": self.vad_clip_count,
            "issues": list(self.issues),
        }


def align_export_subtitles(
    snapshot: dict[str, Any],
    work_dir: Path,
    *,
    cancel_requested: Callable[[], bool],
    transcriber: ASRTranscriber | None = None,
    media_runner: Callable[..., MediaProcessResult] | None = None,
) -> SubtitleAlignmentResult:
    dialogues = [
        item
        for item in snapshot.get("dialogues") or []
        if isinstance(item, dict)
    ]
    targets = [item for item in dialogues if item.get("start_time") is None]
    if str(snapshot.get("subtitle_mode") or "none") == "none" or not targets:
        return SubtitleAlignmentResult(
            status="not_needed",
            provider=None,
            model=None,
            timings={},
            target_count=len(targets),
            attempted_clip_count=0,
            native_timestamp_clip_count=0,
            vad_clip_count=0,
        )

    adapter = transcriber or build_asr_adapter()
    if adapter is None:
        return SubtitleAlignmentResult(
            status="disabled",
            provider=None,
            model=None,
            timings={},
            target_count=len(targets),
            attempted_clip_count=0,
            native_timestamp_clip_count=0,
            vad_clip_count=0,
        )

    runner = media_runner or run_cancellable_media_process
    shot_rows = [
        item for item in snapshot.get("shots") or [] if isinstance(item, dict)
    ]
    asset_rows = [
        item
        for item in snapshot.get("video_assets") or []
        if isinstance(item, dict)
    ]
    dialogues_by_shot: dict[str, list[dict[str, Any]]] = {}
    for dialogue in dialogues:
        shot_id = str(dialogue.get("shot_id") or "")
        if shot_id:
            dialogues_by_shot.setdefault(shot_id, []).append(dialogue)
    for rows in dialogues_by_shot.values():
        rows.sort(
            key=lambda item: (
                int(item.get("sequence_order") or 0),
                str(item.get("id") or ""),
            )
        )

    issues: list[dict[str, str]] = []
    timings: dict[str, AlignedDialogueTiming] = {}
    attempted_clip_count = 0
    native_timestamp_clip_count = 0
    vad_clip_count = 0
    timeline_offset = Decimal("0")
    abort_provider = False
    asr_dir = work_dir / "asr"
    asr_dir.mkdir(parents=True, exist_ok=True)

    for index, shot in enumerate(shot_rows):
        shot_id = str(shot.get("id") or "")
        shot_duration = _decimal(shot.get("duration_sec"), default=Decimal("0"))
        shot_dialogues = dialogues_by_shot.get(shot_id, [])
        shot_targets = [
            item for item in shot_dialogues if item.get("start_time") is None
        ]
        asset = asset_rows[index] if index < len(asset_rows) else None
        if abort_provider or not shot_targets:
            timeline_offset += max(Decimal("0"), shot_duration)
            continue
        if asset is None or not str(asset.get("uri") or ""):
            issues.append(
                _issue("video_missing", shot_id, "片段缺少已采用视频，无法执行 ASR")
            )
            timeline_offset += max(Decimal("0"), shot_duration)
            continue
        if cancel_requested():
            raise MediaProcessCancelled("Media task was cancelled")

        attempted_clip_count += 1
        audio_path = asr_dir / f"{_safe_name(shot_id or str(index))}.wav"
        try:
            _extract_audio(
                str(asset.get("uri")),
                audio_path,
                duration_sec=shot_duration,
                cancel_requested=cancel_requested,
                runner=runner,
            )
            full_transcript = adapter.transcribe(
                audio_path,
                duration_sec=shot_duration,
            )
            tokens = list(full_transcript.tokens)
            if tokens:
                native_timestamp_clip_count += 1
            elif full_transcript.text.strip():
                regions = _detect_voice_regions(
                    audio_path,
                    duration_sec=shot_duration,
                    cancel_requested=cancel_requested,
                    runner=runner,
                )
                if regions:
                    tokens = _transcribe_voice_regions(
                        audio_path,
                        regions,
                        adapter,
                        asr_dir=asr_dir,
                        shot_key=_safe_name(shot_id or str(index)),
                        clip_duration=shot_duration,
                        cancel_requested=cancel_requested,
                        runner=runner,
                    )
                    if tokens:
                        vad_clip_count += 1
                else:
                    issues.append(
                        _issue(
                            "voice_regions_unavailable",
                            shot_id,
                            "ASR 只有纯文本且音频没有可靠静音边界，已回退确定性字幕时间",
                        )
                    )
            else:
                issues.append(
                    _issue(
                        "speech_not_recognized",
                        shot_id,
                        "ASR 未识别到剧本对白，已回退确定性字幕时间",
                    )
                )

            local_timings = align_dialogues_to_tokens(
                shot_dialogues,
                tokens,
                clip_duration=shot_duration,
                min_match_score=float(settings.asr_min_match_score),
            )
            for dialogue_id, timing in local_timings.items():
                dialogue = next(
                    (
                        item
                        for item in shot_targets
                        if str(item.get("id") or "") == dialogue_id
                    ),
                    None,
                )
                if dialogue is None:
                    continue
                timings[dialogue_id] = replace(
                    timing,
                    start_sec=timing.start_sec + timeline_offset,
                    end_sec=timing.end_sec + timeline_offset,
                )
            unmatched = [
                str(item.get("id") or "")
                for item in shot_targets
                if str(item.get("id") or "") not in local_timings
            ]
            if unmatched and tokens:
                issues.append(
                    _issue(
                        "dialogue_match_low_confidence",
                        shot_id,
                        f"{len(unmatched)} 条对白未达到匹配阈值，已回退确定性字幕时间",
                    )
                )
        except ASRProviderError as exc:
            issues.append(_issue(exc.code, shot_id, str(exc)))
            abort_provider = exc.status_code in {401, 403}
        except MediaProcessCancelled:
            raise
        except (OSError, TimeoutError, ValueError) as exc:
            issues.append(
                _issue(
                    "asr_alignment_failed",
                    shot_id,
                    str(exc) or exc.__class__.__name__,
                )
            )
        timeline_offset += max(Decimal("0"), shot_duration)

    aligned_count = len(timings)
    if aligned_count == len(targets):
        alignment_status = "succeeded"
    elif aligned_count:
        alignment_status = "partial"
    else:
        alignment_status = "failed"
    return SubtitleAlignmentResult(
        status=alignment_status,
        provider=getattr(adapter, "name", settings.asr_provider),
        model=getattr(adapter, "model", settings.asr_model),
        timings=timings,
        target_count=len(targets),
        attempted_clip_count=attempted_clip_count,
        native_timestamp_clip_count=native_timestamp_clip_count,
        vad_clip_count=vad_clip_count,
        issues=tuple(issues),
    )


def align_dialogues_to_tokens(
    dialogues: list[dict[str, Any]],
    tokens: list[ASRTimedToken],
    *,
    clip_duration: Decimal,
    min_match_score: float,
) -> dict[str, AlignedDialogueTiming]:
    if not tokens:
        return {}
    ordered_dialogues = sorted(
        dialogues,
        key=lambda item: (
            int(item.get("sequence_order") or 0),
            str(item.get("id") or ""),
        ),
    )
    normalized_tokens = [_normalize_text(item.text) for item in tokens]
    matches: list[AlignedDialogueTiming] = []
    token_cursor = 0
    threshold = min(1.0, max(0.0, min_match_score))
    for dialogue in ordered_dialogues:
        dialogue_id = str(dialogue.get("id") or "")
        expected_parts = _MATCH_TOKEN_RE.findall(str(dialogue.get("text") or ""))
        expected = _normalize_text(" ".join(expected_parts))
        if not dialogue_id or not expected:
            continue
        expected_count = max(1, len(expected_parts))
        best: tuple[float, int, int] | None = None
        minimum_length = max(1, expected_count - 3)
        maximum_length = expected_count + 4
        for start_index in range(token_cursor, len(tokens)):
            available = len(tokens) - start_index
            for length in range(minimum_length, min(maximum_length, available) + 1):
                end_index = start_index + length
                candidate = "".join(normalized_tokens[start_index:end_index])
                if not candidate:
                    continue
                ratio = SequenceMatcher(None, expected, candidate).ratio()
                score = max(0.0, ratio - 0.015 * abs(length - expected_count))
                if best is None or score > best[0]:
                    best = (score, start_index, end_index)
        if best is None or best[0] < threshold:
            continue
        score, start_index, end_index = best
        first = tokens[start_index]
        last = tokens[end_index - 1]
        start_sec = max(Decimal("0"), first.start_sec - _CUE_LEAD_PAD_SEC)
        end_sec = min(clip_duration, last.end_sec + _CUE_TRAIL_PAD_SEC)
        if end_sec <= start_sec:
            continue
        matches.append(
            AlignedDialogueTiming(
                dialogue_id=dialogue_id,
                start_sec=start_sec,
                end_sec=end_sec,
                confidence=round(score, 3),
            )
        )
        token_cursor = end_index

    matches = _separate_adjacent_timings(matches)
    return {item.dialogue_id: item for item in matches}


def _extract_audio(
    uri: str,
    output_path: Path,
    *,
    duration_sec: Decimal,
    cancel_requested: Callable[[], bool],
    runner: Callable[..., MediaProcessResult],
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    args = [
        settings.ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        _media_input(uri),
        "-t",
        _decimal_text(duration_sec),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(output_path),
    ]
    result = runner(
        args,
        cancel_requested=cancel_requested,
        timeout_sec=max(30, int(settings.asr_timeout_sec)),
    )
    if result.returncode != 0 or not output_path.is_file():
        raise ValueError(result.output_tail or "FFmpeg 无法提取片段音频")


def _detect_voice_regions(
    audio_path: Path,
    *,
    duration_sec: Decimal,
    cancel_requested: Callable[[], bool],
    runner: Callable[..., MediaProcessResult],
) -> list[tuple[Decimal, Decimal]]:
    result = runner(
        [
            settings.ffmpeg_path,
            "-hide_banner",
            "-nostats",
            "-i",
            str(audio_path),
            "-af",
            "silencedetect=noise=-35dB:d=0.18",
            "-f",
            "null",
            "-",
        ],
        cancel_requested=cancel_requested,
        timeout_sec=max(30, int(settings.asr_timeout_sec)),
    )
    if result.returncode != 0:
        return []
    events = list(_SILENCE_EVENT_RE.finditer(result.output_tail))
    if not events:
        return []

    silences: list[tuple[Decimal, Decimal]] = []
    silence_start: Decimal | None = None
    for event in events:
        event_time = _decimal(event.group("time"), default=Decimal("0"))
        if event.group("event") == "start":
            silence_start = max(Decimal("0"), event_time)
            continue
        start = silence_start if silence_start is not None else Decimal("0")
        end = min(duration_sec, max(start, event_time))
        if end > start:
            silences.append((start, end))
        silence_start = None
    if silence_start is not None and duration_sec > silence_start:
        silences.append((silence_start, duration_sec))

    regions: list[tuple[Decimal, Decimal]] = []
    cursor = Decimal("0")
    for start, end in sorted(silences):
        start = max(cursor, min(duration_sec, start))
        if start - cursor >= _VOICE_REGION_MIN_SEC:
            regions.append((cursor, start))
        cursor = max(cursor, min(duration_sec, end))
    if duration_sec - cursor >= _VOICE_REGION_MIN_SEC:
        regions.append((cursor, duration_sec))
    return _limit_regions(regions, _MAX_VOICE_REGIONS)


def _transcribe_voice_regions(
    audio_path: Path,
    regions: list[tuple[Decimal, Decimal]],
    transcriber: ASRTranscriber,
    *,
    asr_dir: Path,
    shot_key: str,
    clip_duration: Decimal,
    cancel_requested: Callable[[], bool],
    runner: Callable[..., MediaProcessResult],
) -> list[ASRTimedToken]:
    tokens: list[ASRTimedToken] = []
    for index, (region_start, region_end) in enumerate(regions, start=1):
        if cancel_requested():
            raise MediaProcessCancelled("Media task was cancelled")
        extraction_start = max(Decimal("0"), region_start - _VOICE_REGION_PAD_SEC)
        extraction_end = min(clip_duration, region_end + _VOICE_REGION_PAD_SEC)
        region_duration = extraction_end - extraction_start
        if region_duration <= 0:
            continue
        region_path = asr_dir / f"{shot_key}-region-{index:02d}.wav"
        result = runner(
            [
                settings.ffmpeg_path,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                _decimal_text(extraction_start),
                "-t",
                _decimal_text(region_duration),
                "-i",
                str(audio_path),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(region_path),
            ],
            cancel_requested=cancel_requested,
            timeout_sec=max(30, int(settings.asr_timeout_sec)),
        )
        if result.returncode != 0 or not region_path.is_file():
            continue
        transcript = transcriber.transcribe(
            region_path,
            duration_sec=region_duration,
        )
        if transcript.tokens:
            tokens.extend(
                ASRTimedToken(
                    text=item.text,
                    start_sec=item.start_sec + extraction_start,
                    end_sec=item.end_sec + extraction_start,
                )
                for item in transcript.tokens
            )
        elif transcript.text.strip():
            tokens.extend(
                _interpolate_tokens(
                    transcript.text,
                    start_sec=region_start,
                    end_sec=region_end,
                )
            )
    return sorted(tokens, key=lambda item: (item.start_sec, item.end_sec))


def _interpolate_tokens(
    text: str,
    *,
    start_sec: Decimal,
    end_sec: Decimal,
) -> list[ASRTimedToken]:
    pieces = _MATCH_TOKEN_RE.findall(text)
    if not pieces or end_sec <= start_sec:
        return []
    duration = (end_sec - start_sec) / Decimal(len(pieces))
    return [
        ASRTimedToken(
            text=piece,
            start_sec=start_sec + duration * index,
            end_sec=start_sec + duration * (index + 1),
        )
        for index, piece in enumerate(pieces)
    ]


def _separate_adjacent_timings(
    timings: list[AlignedDialogueTiming],
) -> list[AlignedDialogueTiming]:
    if len(timings) < 2:
        return timings
    adjusted = list(timings)
    for index in range(len(adjusted) - 1):
        current = adjusted[index]
        following = adjusted[index + 1]
        if current.end_sec + _CUE_SEPARATION_SEC <= following.start_sec:
            continue
        boundary = (current.end_sec + following.start_sec) / Decimal("2")
        current_end = max(current.start_sec, boundary - _CUE_SEPARATION_SEC / 2)
        following_start = min(
            following.end_sec,
            boundary + _CUE_SEPARATION_SEC / 2,
        )
        adjusted[index] = replace(current, end_sec=current_end)
        adjusted[index + 1] = replace(following, start_sec=following_start)
    return adjusted


def _limit_regions(
    regions: list[tuple[Decimal, Decimal]],
    limit: int,
) -> list[tuple[Decimal, Decimal]]:
    limited = list(regions)
    while len(limited) > max(1, limit):
        merge_index = min(
            range(len(limited) - 1),
            key=lambda index: limited[index + 1][0] - limited[index][1],
        )
        limited[merge_index : merge_index + 2] = [
            (limited[merge_index][0], limited[merge_index + 1][1])
        ]
    return limited


def _media_input(uri: str) -> str:
    if uri.startswith("mock://"):
        raise ValueError("Mock media cannot be aligned")
    try:
        return str(get_media_store().resolve_local_path(uri))
    except (FileNotFoundError, ValueError):
        return uri


def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text).lower()
    return "".join(character for character in normalized if character.isalnum())


def _decimal(value: Any, *, default: Decimal) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return default


def _decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _safe_name(value: str) -> str:
    normalized = "".join(
        character for character in value if character.isalnum() or character in "-_"
    )
    return normalized or "shot"


def _issue(code: str, shot_id: str, message: str) -> dict[str, str]:
    return {
        "code": str(code or "asr_alignment_failed")[:120],
        "shot_id": shot_id,
        "message": str(message or "ASR alignment failed")[:500],
    }
