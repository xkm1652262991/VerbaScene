from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any, Callable

from app.providers.base import ProviderAdapter
from app.providers.types import ProviderRequest, ProviderResponse, ProviderStatus, SubmissionState
from app.production.shot_direction.contracts import (
    SHOT_DIRECTION_PIPELINE_VERSION,
    ShotDirectionInput,
    build_shot_contract_report,
    parse_and_apply_shot_patch,
    parse_reflection_response,
    parse_shot_draft_response,
    reflection_requires_patch,
    review_issue_counts,
)
from app.production.shot_direction.prompts import (
    build_reflection_prompt,
    build_shot_draft_prompt,
    build_shot_patch_prompt,
    build_shot_structure_recovery_prompt,
)


CheckpointCallback = Callable[[dict[str, Any], str, int], None]


@dataclass(frozen=True)
class ShotDirectionPipelineResult:
    asset_report: dict[str, Any]
    draft: list[dict[str, Any]]
    review: dict[str, Any]
    patch: dict[str, Any] | None
    final: list[dict[str, Any]]
    contract_report: dict[str, Any]
    checkpoint: dict[str, Any]
    patched_shot_nos: list[int]

    @property
    def patch_applied(self) -> bool:
        return bool(self.checkpoint.get("patch_applied"))

    @property
    def quality_gate(self) -> str:
        return str(self.contract_report.get("quality_gate") or "needs_attention")

    @property
    def issue_counts(self) -> dict[str, int]:
        return review_issue_counts(self.review)

    @property
    def resolved_issue_codes(self) -> list[str]:
        return list(self.patch.get("resolved_issue_codes") or []) if self.patch else []

    @property
    def unresolved_issue_codes(self) -> list[str]:
        return list(self.contract_report.get("unresolved_issue_codes") or [])


class ShotDirectionPipelineFailure(RuntimeError):
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


def run_shot_direction_pipeline(
    *,
    provider: ProviderAdapter,
    review_provider: ProviderAdapter | None,
    source: ShotDirectionInput,
    asset_report: dict[str, Any],
    task_id: str,
    model: str,
    review_model: str | None,
    system_prompt: str,
    review_system_prompt: str | None,
    temperatures: dict[str, float],
    retry_not_submitted: bool,
    checkpoint: dict[str, Any] | None = None,
    on_checkpoint: CheckpointCallback | None = None,
) -> ShotDirectionPipelineResult:
    state = _initial_checkpoint(checkpoint, asset_report)
    _recover_inflight_phase(state)
    critic = review_provider or provider
    critic_model = review_model or model
    critic_system_prompt = review_system_prompt or system_prompt

    if not isinstance(state.get("draft"), list):
        draft_record = _phase_response(
            state,
            provider=provider,
            source=source,
            task_id=task_id,
            model=model,
            system_prompt=system_prompt,
            phase="storyboard_draft",
            prompt=build_shot_draft_prompt(source),
            temperature=temperatures["storyboard_draft"],
            on_checkpoint=on_checkpoint,
            progress=30,
        )
        _require_success(state, "storyboard_draft", draft_record)
        try:
            draft = parse_shot_draft_response(_record_text(draft_record), source)
        except (ValueError, json.JSONDecodeError) as draft_exc:
            _add_fallback(state, "storyboard_draft", str(draft_exc), "structure_recovery")
            recovery_record = _phase_response(
                state,
                provider=provider,
                source=source,
                task_id=task_id,
                model=model,
                system_prompt=system_prompt,
                phase="storyboard_structure_recovery",
                prompt=build_shot_structure_recovery_prompt(
                    source,
                    malformed_text=_record_text(draft_record),
                    error=str(draft_exc),
                ),
                temperature=temperatures["structure_recovery"],
                on_checkpoint=on_checkpoint,
                progress=43,
            )
            _require_success(state, "storyboard_structure_recovery", recovery_record)
            try:
                draft = parse_shot_draft_response(_record_text(recovery_record), source)
            except (ValueError, json.JSONDecodeError) as recovery_exc:
                raise ShotDirectionPipelineFailure(
                    phase="storyboard_structure_recovery",
                    code="shot_breakdown_parse_failed",
                    message=f"ShotDraft structure recovery failed: {recovery_exc}",
                    checkpoint=deepcopy(state),
                    provider_task_id=recovery_record.get("provider_task_id"),
                ) from recovery_exc
        state["draft"] = draft
        state["draft_contract_report"] = build_shot_contract_report(
            shots=draft,
            source=source,
            review_available=True,
        )
        _publish(state, on_checkpoint, "分镜导演初稿与确定性检查已保存", 50)

    draft = deepcopy(state["draft"])
    draft_contract = dict(state.get("draft_contract_report") or {})

    if not isinstance(state.get("review"), dict):
        try:
            review_record = _phase_response(
                state,
                provider=critic,
                source=source,
                task_id=task_id,
                model=critic_model,
                system_prompt=critic_system_prompt,
                phase="storyboard_reflection",
                prompt=build_reflection_prompt(
                    source,
                    asset_report=asset_report,
                    draft=draft,
                    draft_contract=draft_contract,
                ),
                temperature=temperatures["storyboard_reflection"],
                on_checkpoint=on_checkpoint,
                progress=64,
            )
        except ShotDirectionPipelineFailure as exc:
            state = deepcopy(exc.checkpoint)
            if exc.submission_state == SubmissionState.UNKNOWN:
                state.pop("inflight_phase", None)
                _degrade_review(state, exc.message)
                review_record = None
            else:
                raise

        review_available = False
        review: dict[str, Any] = {"summary": "", "issues": []}
        if review_record is not None and review_record["status"] == ProviderStatus.SUCCEEDED.value:
            try:
                review = parse_reflection_response(
                    _record_text(review_record),
                    draft=draft,
                    source=source,
                    asset_report=asset_report,
                    draft_contract=draft_contract,
                )
                review_available = True
            except (ValueError, json.JSONDecodeError) as exc:
                _degrade_review(state, f"ReflectionReport parse failed: {exc}")
        elif review_record is not None:
            if _should_retry_not_submitted(review_record, retry_not_submitted):
                _require_success(state, "storyboard_reflection", review_record)
            _degrade_review(
                state,
                review_record.get("error_message") or "分镜审稿 Provider 不可用",
            )
        if not review_available:
            review = {"summary": "审稿不可用，保留确定性合同有效的分镜草案。", "issues": []}
        state["review"] = review
        state["review_available"] = review_available
        _publish(state, on_checkpoint, "分镜审稿报告已保存", 71)

    review = dict(state["review"])
    review_available = bool(state.get("review_available", False))

    if not isinstance(state.get("final"), list):
        final = deepcopy(draft)
        patch: dict[str, Any] | None = None
        patch_applied = False
        patched_shot_nos: list[int] = []
        if review_available and reflection_requires_patch(review):
            try:
                patch_record = _phase_response(
                    state,
                    provider=provider,
                    source=source,
                    task_id=task_id,
                    model=model,
                    system_prompt=system_prompt,
                    phase="storyboard_patch",
                    prompt=build_shot_patch_prompt(source, draft=draft, review=review),
                    temperature=temperatures["storyboard_patch"],
                    on_checkpoint=on_checkpoint,
                    progress=82,
                )
            except ShotDirectionPipelineFailure as exc:
                state = deepcopy(exc.checkpoint)
                if exc.submission_state == SubmissionState.UNKNOWN:
                    state.pop("inflight_phase", None)
                    _degrade_patch(state, exc.message)
                    patch_record = None
                else:
                    raise
            if patch_record is not None and patch_record["status"] == ProviderStatus.SUCCEEDED.value:
                try:
                    patch_result = parse_and_apply_shot_patch(
                        _record_text(patch_record),
                        draft=draft,
                        review=review,
                        source=source,
                    )
                    patch = patch_result.patch
                    final = patch_result.shots
                    patched_shot_nos = patch_result.patched_shot_nos
                    patch_applied = bool(patch.get("operations"))
                except (ValueError, json.JSONDecodeError) as exc:
                    _degrade_patch(state, f"ShotPatch rejected: {exc}")
            elif patch_record is not None:
                if _should_retry_not_submitted(patch_record, retry_not_submitted):
                    _require_success(state, "storyboard_patch", patch_record)
                _degrade_patch(
                    state,
                    patch_record.get("error_message") or "定点修订 Provider 不可用",
                )
        state["patch"] = patch
        state["final"] = final
        state["patch_applied"] = patch_applied
        state["patched_shot_nos"] = patched_shot_nos
        _publish(state, on_checkpoint, "最终分镜候选已保存", 89)

    final = deepcopy(state["final"])
    patch = dict(state["patch"]) if isinstance(state.get("patch"), dict) else None
    if not isinstance(state.get("contract_report"), dict):
        contract_report = build_shot_contract_report(
            shots=final,
            source=source,
            review=review,
            patch=patch,
            review_available=review_available,
            patch_error=str(state.get("patch_error") or "") or None,
        )
        state["contract_report"] = contract_report
        state["quality_gate"] = contract_report["quality_gate"]
        _publish(state, on_checkpoint, "最终分镜生产合同已校验", 94)
    contract_report = dict(state["contract_report"])
    if not contract_report.get("valid"):
        raise ShotDirectionPipelineFailure(
            phase="final_contract",
            code="shot_contract_invalid",
            message="最终分镜生产合同未通过，当前批次保持不变",
            checkpoint=deepcopy(state),
            provider_task_id=_last_provider_task_id(state),
        )

    return ShotDirectionPipelineResult(
        asset_report=deepcopy(asset_report),
        draft=draft,
        review=review,
        patch=patch,
        final=final,
        contract_report=contract_report,
        checkpoint=deepcopy(state),
        patched_shot_nos=[int(value) for value in state.get("patched_shot_nos") or []],
    )


def _initial_checkpoint(value: dict[str, Any] | None, asset_report: dict[str, Any]) -> dict[str, Any]:
    state = deepcopy(value) if isinstance(value, dict) else {}
    if state.get("pipeline_version") != SHOT_DIRECTION_PIPELINE_VERSION:
        state = {}
    state.setdefault("pipeline_version", SHOT_DIRECTION_PIPELINE_VERSION)
    state.setdefault("responses", {})
    state.setdefault("pipeline_trace", {"phases": [], "fallbacks": []})
    state.setdefault("asset_report", deepcopy(asset_report))
    return state


def _recover_inflight_phase(state: dict[str, Any]) -> None:
    inflight = state.get("inflight_phase")
    if not isinstance(inflight, dict):
        return
    phase = str(inflight.get("phase") or "provider_submission")
    if isinstance(state.get("responses", {}).get(phase), dict):
        state.pop("inflight_phase", None)
        return
    if phase == "storyboard_reflection":
        state.pop("inflight_phase", None)
        _degrade_review(
            state,
            "Reflection submission was interrupted before its response was durably recorded",
        )
        state["review"] = {"summary": "审稿提交结果不确定，保留有效草案。", "issues": []}
        state["review_available"] = False
        return
    if phase == "storyboard_patch":
        state.pop("inflight_phase", None)
        _degrade_patch(
            state,
            "ShotPatch submission was interrupted before its response was durably recorded",
        )
        if isinstance(state.get("draft"), list):
            state["final"] = deepcopy(state["draft"])
            state["patch"] = None
            state["patch_applied"] = False
            state["patched_shot_nos"] = []
        return
    raise ShotDirectionPipelineFailure(
        phase=phase,
        code="provider_submission_uncertain",
        message=f"The {phase} provider submission result is uncertain",
        checkpoint=deepcopy(state),
        retryable=False,
        submission_state=SubmissionState.UNKNOWN,
    )


def _phase_response(
    state: dict[str, Any],
    *,
    provider: ProviderAdapter,
    source: ShotDirectionInput,
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
    }
    _publish(state, on_checkpoint, f"{phase} 正在提交 Provider", max(1, progress - 1))
    try:
        response = provider.submit(
            ProviderRequest(
                project_id=source.project_id,
                task_id=f"{task_id}:{phase}",
                model=model,
                prompt=prompt,
                system_prompt=system_prompt,
                params={"temperature": temperature},
                metadata={
                    "stage": "shot_breakdown",
                    "phase": phase,
                    "pipeline_version": SHOT_DIRECTION_PIPELINE_VERSION,
                },
            )
        )
    except Exception as exc:
        raise ShotDirectionPipelineFailure(
            phase=phase,
            code=f"{phase}_submit_interrupted",
            message=str(exc) or f"{phase} provider submission was interrupted",
            checkpoint=deepcopy(state),
            retryable=False,
            submission_state=SubmissionState.UNKNOWN,
        ) from exc
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
    raise ShotDirectionPipelineFailure(
        phase=phase,
        code=record.get("error_code") or f"{phase}_failed",
        message=record.get("error_message") or f"{phase} provider failed",
        checkpoint=deepcopy(state),
        provider_task_id=record.get("provider_task_id"),
        retryable=bool(record.get("retryable")),
        submission_state=_submission_state(record.get("submission_state")),
    )


def _should_retry_not_submitted(record: dict[str, Any], retry_not_submitted: bool) -> bool:
    return (
        retry_not_submitted
        and bool(record.get("retryable"))
        and _submission_state(record.get("submission_state")) == SubmissionState.NOT_SUBMITTED
    )


def _degrade_review(state: dict[str, Any], reason: str) -> None:
    _add_fallback(state, "storyboard_reflection", reason, "keep_valid_draft")
    state["review_available"] = False


def _degrade_patch(state: dict[str, Any], reason: str) -> None:
    _add_fallback(state, "storyboard_patch", reason, "reject_patch_keep_valid_draft")
    state["patch_error"] = reason


def _record_text(record: dict[str, Any]) -> str:
    raw_response = record.get("raw_response")
    return str(raw_response.get("text", "")) if isinstance(raw_response, dict) else ""


def _submission_state(value: object) -> SubmissionState:
    try:
        return SubmissionState(str(value))
    except ValueError:
        return SubmissionState.UNKNOWN


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


def _last_provider_task_id(checkpoint: dict[str, Any]) -> str | None:
    trace = checkpoint.get("pipeline_trace") if isinstance(checkpoint, dict) else None
    phases = trace.get("phases") if isinstance(trace, dict) else None
    if not isinstance(phases, list):
        return None
    for item in reversed(phases):
        if isinstance(item, dict) and item.get("provider_task_id"):
            return str(item["provider_task_id"])
    return None
