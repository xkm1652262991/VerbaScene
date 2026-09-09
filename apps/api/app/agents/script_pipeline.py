from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any, Callable

from app.agents.script_contracts import (
    SCRIPT_QUALITY_PIPELINE_VERSION,
    ScriptGenerationInput,
    apply_script_patch,
    augment_script_review,
    build_contract_report,
    fallback_story_blueprint,
    parse_script_patch_response,
    parse_script_review_response,
    parse_story_blueprint_response,
    required_phrases_from_source,
    review_issue_counts,
    review_requires_revision,
)
from app.agents.script_prompts import (
    build_script_draft_prompt,
    build_script_patch_prompt,
    build_script_review_prompt,
    build_script_structure_recovery_prompt,
    build_story_blueprint_prompt,
)
from app.agents.script_screenplay import parse_screenplay_response
from app.providers.base import ProviderAdapter
from app.providers.openai_chat_params import structured_json_params
from app.providers.types import (
    ProviderRequest,
    ProviderResponse,
    ProviderStatus,
    SubmissionState,
)


CheckpointCallback = Callable[[dict[str, Any], str, int], None]


@dataclass(frozen=True)
class ScriptPipelineResult:
    blueprint: dict[str, Any]
    draft: dict[str, Any]
    review: dict[str, Any]
    patch: dict[str, Any] | None
    final: dict[str, Any]
    contract_report: dict[str, Any]
    checkpoint: dict[str, Any]

    @property
    def patch_applied(self) -> bool:
        return bool(self.checkpoint.get("patch_applied"))

    @property
    def unresolved_issue_count(self) -> int:
        return len(self.contract_report.get("unresolved_issue_codes") or [])

    @property
    def quality_gate(self) -> str:
        return str(self.contract_report.get("quality_gate") or "needs_attention")

    @property
    def issue_counts(self) -> dict[str, int]:
        return review_issue_counts(self.review)

    @property
    def patched_scene_nos(self) -> list[int]:
        if not isinstance(self.patch, dict):
            return []
        return [
            int(scene["scene_no"])
            for scene in self.patch.get("scene_replacements") or []
            if isinstance(scene, dict) and isinstance(scene.get("scene_no"), int)
        ]

    @property
    def resolved_issue_codes(self) -> list[str]:
        return list(self.patch.get("resolved_issue_codes") or []) if isinstance(self.patch, dict) else []

    @property
    def unresolved_issue_codes(self) -> list[str]:
        return list(self.contract_report.get("unresolved_issue_codes") or [])


class ScriptPipelineFailure(RuntimeError):
    def __init__(
        self,
        *,
        phase: str,
        code: str,
        message: str,
        checkpoint: dict[str, Any],
        provider_task_id: str | None = None,
        retryable: bool = False,
        submission_state: SubmissionState = SubmissionState.ACCEPTED,
    ) -> None:
        super().__init__(message)
        self.phase = phase
        self.code = code
        self.message = message
        self.checkpoint = checkpoint
        self.provider_task_id = provider_task_id
        self.retryable = retryable
        self.submission_state = submission_state


def run_script_pipeline(
    *,
    provider: ProviderAdapter,
    review_provider: ProviderAdapter | None = None,
    source: ScriptGenerationInput,
    task_id: str,
    model: str,
    review_model: str | None = None,
    system_prompt: str,
    review_system_prompt: str | None = None,
    temperatures: dict[str, float],
    checkpoint: dict[str, Any] | None = None,
    on_checkpoint: CheckpointCallback | None = None,
) -> ScriptPipelineResult:
    state = _initial_checkpoint(checkpoint)
    inflight = state.get("inflight_phase")
    if isinstance(inflight, dict):
        inflight_phase = str(inflight.get("phase") or "provider_submission")
        if not isinstance(state.get("responses", {}).get(inflight_phase), dict):
            raise ScriptPipelineFailure(
                phase=inflight_phase,
                code="provider_submission_uncertain",
                message=(
                    f"The {inflight_phase} provider submission was interrupted "
                    "before its response was durably recorded"
                ),
                checkpoint=deepcopy(state),
                retryable=False,
                submission_state=SubmissionState.UNKNOWN,
            )
        state.pop("inflight_phase", None)
    critic = review_provider or provider
    critic_model = review_model or model
    critic_system_prompt = review_system_prompt or system_prompt

    if not isinstance(state.get("blueprint"), dict):
        record = _phase_response(
            state,
            provider=provider,
            source=source,
            task_id=task_id,
            model=model,
            system_prompt=system_prompt,
            phase="story_blueprint",
            prompt=build_story_blueprint_prompt(source),
            temperature=temperatures["story_blueprint"],
            on_checkpoint=on_checkpoint,
            progress=18,
        )
        try:
            if record["status"] != ProviderStatus.SUCCEEDED.value:
                raise ValueError(record.get("error_message") or "故事开发调用失败")
            blueprint = parse_story_blueprint_response(_record_text(record))
        except (ValueError, json.JSONDecodeError) as exc:
            _add_fallback(state, "story_blueprint", str(exc), "use_source_grounded_blueprint")
            blueprint = fallback_story_blueprint(source, reason=str(exc))
        blueprint["required_phrases"] = list(
            dict.fromkeys(
                [
                    *(blueprint.get("required_phrases") or []),
                    *required_phrases_from_source(source),
                ]
            )
        )
        state["blueprint"] = blueprint
        _publish(state, on_checkpoint, "故事架构 Agent 产物已保存", 28)

    blueprint = dict(state["blueprint"])

    if not isinstance(state.get("draft"), dict):
        draft_record = _phase_response(
            state,
            provider=provider,
            source=source,
            task_id=task_id,
            model=model,
            system_prompt=system_prompt,
            phase="script_draft",
            prompt=build_script_draft_prompt(source, blueprint),
            temperature=temperatures["script_draft"],
            on_checkpoint=on_checkpoint,
            progress=38,
        )
        _require_success(state, "script_draft", draft_record)
        try:
            draft = parse_screenplay_response(_record_text(draft_record))
        except (ValueError, json.JSONDecodeError) as draft_exc:
            _add_fallback(state, "script_draft", str(draft_exc), "structure_recovery")
            recovery_record = _phase_response(
                state,
                provider=provider,
                source=source,
                task_id=task_id,
                model=model,
                system_prompt=system_prompt,
                phase="structure_recovery",
                prompt=build_script_structure_recovery_prompt(
                    source,
                    blueprint,
                    _record_text(draft_record),
                ),
                temperature=temperatures["structure_recovery"],
                on_checkpoint=on_checkpoint,
                progress=48,
            )
            _require_success(state, "structure_recovery", recovery_record)
            try:
                draft = parse_screenplay_response(_record_text(recovery_record))
            except (ValueError, json.JSONDecodeError) as recovery_exc:
                raise ScriptPipelineFailure(
                    phase="structure_recovery",
                    code="script_generation_parse_failed",
                    message=f"Script generation JSON parse failed: {recovery_exc}",
                    checkpoint=deepcopy(state),
                    provider_task_id=recovery_record.get("provider_task_id"),
                ) from recovery_exc
        state["draft"] = draft
        _publish(state, on_checkpoint, "主创编剧 Agent 初稿已保存", 56)

    draft = dict(state["draft"])

    if not isinstance(state.get("review"), dict):
        review_record = _phase_response(
            state,
            provider=critic,
            source=source,
            task_id=task_id,
            model=critic_model,
            system_prompt=critic_system_prompt,
            phase="script_review",
            prompt=build_script_review_prompt(source, blueprint, draft["scenes"]),
            temperature=temperatures["script_review"],
            on_checkpoint=on_checkpoint,
            progress=66,
        )
        review: dict[str, Any] = {
            "issues": [],
        }
        review_available = False
        if review_record["status"] == ProviderStatus.SUCCEEDED.value:
            try:
                review = parse_script_review_response(_record_text(review_record))
                review_available = True
            except (ValueError, json.JSONDecodeError) as exc:
                _add_fallback(state, "script_review", str(exc), "keep_valid_draft")
        else:
            _add_fallback(
                state,
                "script_review",
                review_record.get("error_message") or "审稿调用失败",
                "keep_valid_draft",
            )
        state["review"] = augment_script_review(
            review,
            draft_scenes=draft["scenes"],
            blueprint=blueprint,
        )
        state["review_available"] = review_available
        _publish(state, on_checkpoint, "综合审稿 Agent 产物已保存", 72)

    review = dict(state["review"])
    review_available = bool(state.get("review_available", False))

    if not isinstance(state.get("final"), dict):
        final = draft
        patch: dict[str, Any] | None = None
        patch_applied = False
        if review_available and review_requires_revision(review):
            patch_record = _phase_response(
                state,
                provider=provider,
                source=source,
                task_id=task_id,
                model=model,
                system_prompt=system_prompt,
                phase="script_patch",
                prompt=build_script_patch_prompt(source, blueprint, draft["scenes"], review),
                temperature=temperatures["script_patch"],
                on_checkpoint=on_checkpoint,
                progress=82,
            )
            if patch_record["status"] == ProviderStatus.SUCCEEDED.value:
                try:
                    patch = parse_script_patch_response(
                        _record_text(patch_record),
                        draft_scenes=draft["scenes"],
                        review=review,
                    )
                    final = apply_script_patch(draft, patch)
                    patch_applied = bool(patch["scene_replacements"])
                except (ValueError, json.JSONDecodeError) as exc:
                    _add_fallback(state, "script_patch", str(exc), "reject_patch_keep_valid_draft")
                    state["patch_error"] = str(exc)
            else:
                _add_fallback(
                    state,
                    "script_patch",
                    patch_record.get("error_message") or "定点修订调用失败",
                    "keep_valid_draft",
                )
                state["patch_error"] = patch_record.get("error_message") or "定点修订调用失败"
        state["patch"] = patch
        state["final"] = final
        state["patch_applied"] = patch_applied
        _publish(state, on_checkpoint, "最终剧本候选已保存", 90)

    final = dict(state["final"])
    patch = dict(state["patch"]) if isinstance(state.get("patch"), dict) else None
    if not isinstance(state.get("contract_report"), dict):
        state["contract_report"] = build_contract_report(
            final=final,
            blueprint=blueprint,
            review=review,
            patch=patch,
            review_available=review_available,
        )
        if state.get("patch_error") and state["contract_report"]["quality_gate"] == "pass":
            state["contract_report"]["quality_gate"] = "needs_attention"
        state["quality_gate"] = state["contract_report"]["quality_gate"]
        _publish(state, on_checkpoint, "确定性生产合同已校验", 94)

    return ScriptPipelineResult(
        blueprint=blueprint,
        draft=draft,
        review=review,
        patch=patch,
        final=final,
        contract_report=dict(state["contract_report"]),
        checkpoint=deepcopy(state),
    )


def _initial_checkpoint(value: dict[str, Any] | None) -> dict[str, Any]:
    state = deepcopy(value) if isinstance(value, dict) else {}
    if state.get("pipeline_version") != SCRIPT_QUALITY_PIPELINE_VERSION:
        state = {}
    state.setdefault("pipeline_version", SCRIPT_QUALITY_PIPELINE_VERSION)
    state.setdefault("responses", {})
    state.setdefault("pipeline_trace", {"phases": [], "fallbacks": []})
    return state


def _phase_response(
    state: dict[str, Any],
    *,
    provider: ProviderAdapter,
    source: ScriptGenerationInput,
    task_id: str,
    model: str,
    system_prompt: str,
    phase: str,
    prompt: str,
    temperature: float,
    on_checkpoint: CheckpointCallback | None,
    progress: int,
) -> dict[str, Any]:
    responses = state["responses"]
    existing = responses.get(phase)
    if isinstance(existing, dict):
        return existing

    state["inflight_phase"] = {
        "phase": phase,
        "attempted_at": datetime.now(timezone.utc).isoformat(),
        "submission_state": SubmissionState.NOT_SUBMITTED.value,
    }
    _publish(
        state,
        on_checkpoint,
        f"{phase} 正在提交 Provider",
        max(1, progress - 1),
    )
    response = provider.submit_streaming(
        ProviderRequest(
            project_id=source.project_id,
            task_id=f"{task_id}:{phase}",
            model=model,
            prompt=prompt,
            system_prompt=system_prompt,
            params=structured_json_params(temperature=temperature),
            metadata={
                "stage": "script_generation",
                "phase": phase,
                "pipeline_version": SCRIPT_QUALITY_PIPELINE_VERSION,
            },
        ),
        on_progress=_stream_progress_callback(
            state,
            phase=phase,
            callback=on_checkpoint,
            progress=max(1, progress - 1),
        ),
    )
    record = _response_record(
        response,
        provider=provider.name,
        model=model,
        temperature=temperature,
    )
    responses[phase] = record
    state.pop("inflight_phase", None)
    state["pipeline_trace"]["phases"].append(
        {
            "phase": phase,
            "status": record["status"],
            "provider_task_id": record["provider_task_id"],
            "provider": record["provider"],
            "model": record["model"],
            "temperature": temperature,
            "response_format": "json_object",
            "streaming": "streaming" in provider.capabilities,
        }
    )
    _publish(state, on_checkpoint, f"{phase} 模型响应已保存", progress)
    return record


def _response_record(
    response: ProviderResponse,
    *,
    provider: str,
    model: str,
    temperature: float,
) -> dict[str, Any]:
    return {
        "status": response.status.value,
        "provider_task_id": response.provider_task_id,
        "provider": provider,
        "model": model,
        "temperature": temperature,
        "raw_response": response.raw_response,
        "error_code": response.error.error_code if response.error else None,
        "error_message": response.error.error_message if response.error else None,
        "retryable": response.error.is_retryable if response.error else False,
        "submission_state": (
            response.error.submission_state.value
            if response.error
            else (
                SubmissionState.ACCEPTED.value
                if response.provider_task_id
                else SubmissionState.UNKNOWN.value
            )
        ),
    }


def _require_success(state: dict[str, Any], phase: str, record: dict[str, Any]) -> None:
    if record["status"] == ProviderStatus.SUCCEEDED.value:
        return
    raise ScriptPipelineFailure(
        phase=phase,
        code=record.get("error_code") or f"{phase}_failed",
        message=record.get("error_message") or f"{phase} provider failed",
        checkpoint=deepcopy(state),
        provider_task_id=record.get("provider_task_id"),
        retryable=bool(record.get("retryable")),
        submission_state=_submission_state(record.get("submission_state")),
    )


def _submission_state(value: object) -> SubmissionState:
    try:
        return SubmissionState(str(value))
    except ValueError:
        return SubmissionState.UNKNOWN


def _record_text(record: dict[str, Any]) -> str:
    raw_response = record.get("raw_response")
    return str(raw_response.get("text", "")) if isinstance(raw_response, dict) else ""


def _add_fallback(state: dict[str, Any], phase: str, reason: str, action: str) -> None:
    fallbacks = state["pipeline_trace"]["fallbacks"]
    item = {"phase": phase, "reason": reason, "action": action}
    if item not in fallbacks:
        fallbacks.append(item)


def _publish(
    state: dict[str, Any],
    callback: CheckpointCallback | None,
    label: str,
    progress: int,
) -> None:
    if callback is not None:
        callback(deepcopy(state), label, progress)


def _stream_progress_callback(
    state: dict[str, Any],
    *,
    phase: str,
    callback: CheckpointCallback | None,
    progress: int,
) -> Callable[[dict[str, Any]], None] | None:
    if callback is None:
        return None

    def publish_stream_progress(event: dict[str, Any]) -> None:
        inflight = state.get("inflight_phase")
        if not isinstance(inflight, dict) or inflight.get("phase") != phase:
            return
        inflight["submission_state"] = str(
            event.get("submission_state") or SubmissionState.ACCEPTED.value
        )
        inflight["stream_progress"] = {
            "event": str(event.get("event") or "stream_delta"),
            "elapsed_sec": float(event.get("elapsed_sec") or 0),
            "chunk_count": int(event.get("chunk_count") or 0),
            "content_chars": int(event.get("content_chars") or 0),
            "reasoning_chars": int(event.get("reasoning_chars") or 0),
        }
        detail = inflight["stream_progress"]
        _publish(
            state,
            callback,
            (
                f"{phase} 流式生成中 · {detail['elapsed_sec']:.0f}s · "
                f"正文 {detail['content_chars']} 字 / 推理 {detail['reasoning_chars']} 字"
            ),
            progress,
        )

    return publish_stream_progress
