from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from typing import Any, Iterator
from unittest.mock import patch

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker


API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from app.db.session import create_database_engine, initialize_database  # noqa: E402
from app.evals.resume_benchmark import (  # noqa: E402
    BENCHMARK_VERSION,
    content_hash,
    percentile,
    write_csv,
    write_json,
)
from app.exports.subtitle_alignment import (  # noqa: E402
    align_dialogues_to_tokens,
    align_export_subtitles,
)
from app.models import Asset, AssetCandidate, GenerationTask, Project, Shot  # noqa: E402
from app.platform.tasks.repository import (  # noqa: E402
    TaskConflictError,
    TaskRepository,
)
from app.platform.tasks.runtime import LocalTaskRuntime  # noqa: E402
from app.platform.tasks.types import (  # noqa: E402
    SubmissionState,
    TaskExecutionError,
    TaskStatus,
)
from app.production.contracts import VIDEO_CANDIDATE_TASK_TYPE  # noqa: E402
from app.production.video_candidate_persistence import (  # noqa: E402
    persist_video_candidate_task_result,
)
from app.production.video_task_handler import VideoCandidateTaskHandler  # noqa: E402
from app.providers.base import ProviderAdapter  # noqa: E402
from app.providers.mock import MockImageProvider, MockLLMProvider, MockVideoProvider  # noqa: E402
from app.platform.media.subprocess_runner import MediaProcessResult  # noqa: E402
from app.providers.openai_compatible_asr import (  # noqa: E402
    ASRProviderError,
    ASRTimedToken,
    ASRTranscript,
)
from app.providers.types import (  # noqa: E402
    ProviderAsset,
    ProviderError,
    ProviderExecutionMode,
    ProviderRequest,
    ProviderResponse,
    ProviderStatus,
    ProviderType,
)


FAULT_REPETITIONS = 10


class LifecycleDouble(ProviderAdapter):
    name = "lifecycle-contract-double"
    type = ProviderType.VIDEO
    model = "contract-double-v1"
    capabilities = ["async", "poll", "cancel", "fetch_result"]
    native_audio = True
    supported_resolutions = ["854x480", "480x854"]

    def __init__(self, spec: dict[str, Any]) -> None:
        self.spec = spec
        self.poll_index = 0
        self.fetch_index = 0
        self.calls: list[dict[str, Any]] = []

    def submit(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append({"operation": "submit", "task_id": request.task_id})
        return _contract_response(self.spec["submit"], "contract-remote-1")

    def poll(self, provider_task_id: str) -> ProviderResponse:
        self.calls.append({"operation": "poll", "provider_task_id": provider_task_id})
        values = self.spec.get("polls") or [self.spec["submit"]]
        value = values[min(self.poll_index, len(values) - 1)]
        self.poll_index += 1
        return _contract_response(value, provider_task_id)

    def fetch_result(
        self,
        provider_task_id: str,
        *,
        request: ProviderRequest | None = None,
        provider_context: dict | None = None,
    ) -> ProviderResponse:
        self.calls.append(
            {
                "operation": "fetch_result",
                "provider_task_id": provider_task_id,
                "request_present": request is not None,
                "context_present": provider_context is not None,
            }
        )
        values = self.spec.get("fetches") or ["succeeded_asset"]
        value = values[min(self.fetch_index, len(values) - 1)]
        self.fetch_index += 1
        return _contract_response(value, provider_task_id)

    def cancel(self, provider_task_id: str) -> None:
        self.calls.append({"operation": "cancel", "provider_task_id": provider_task_id})
        if self.spec.get("cancel_unsupported"):
            raise NotImplementedError("contract double cancellation unsupported")
        if self.spec.get("cancel_error"):
            raise OSError("contract double cancellation transport error")


class AckLostVideoProvider(ProviderAdapter):
    name = "fault-double"
    type = ProviderType.VIDEO
    model = "fault-double-v1"
    capabilities = ["async", "poll", "cancel"]

    def __init__(self) -> None:
        self.submit_count = 0

    def submit(self, request: ProviderRequest) -> ProviderResponse:
        _ = request
        self.submit_count += 1
        raise TimeoutError("ack lost after remote acceptance")


class PollRecoveryVideoProvider(ProviderAdapter):
    name = "fault-double"
    type = ProviderType.VIDEO
    model = "fault-double-v1"
    capabilities = ["async", "poll", "cancel"]

    def __init__(self) -> None:
        self.submit_count = 0
        self.poll_count = 0
        self.fetch_count = 0

    def submit(self, request: ProviderRequest) -> ProviderResponse:
        _ = request
        self.submit_count += 1
        raise AssertionError("restart with provider_task_id must not resubmit")

    def poll(self, provider_task_id: str) -> ProviderResponse:
        self.poll_count += 1
        if self.poll_count == 1:
            raise OSError("worker lost provider poll response")
        return ProviderResponse(
            status=ProviderStatus.SUCCEEDED,
            provider_task_id=provider_task_id,
            execution_mode=ProviderExecutionMode.ASYNC,
            raw_response={"status": "succeeded"},
        )

    def fetch_result(self, provider_task_id: str, **_kwargs: Any) -> ProviderResponse:
        self.fetch_count += 1
        return _successful_video_response(provider_task_id)


class CancelRaceVideoProvider(PollRecoveryVideoProvider):
    def __init__(self, factory: sessionmaker[Session], task_id: str) -> None:
        super().__init__()
        self.factory = factory
        self.task_id = task_id
        self.cancel_count = 0

    def poll(self, provider_task_id: str) -> ProviderResponse:
        self.poll_count += 1
        with self.factory() as db:
            task = db.get(GenerationTask, self.task_id)
            if task is not None:
                task.cancel_requested_at = datetime.now(timezone.utc)
                task.status = TaskStatus.CANCELLING.value
                db.add(task)
                db.commit()
        return ProviderResponse(
            status=ProviderStatus.SUCCEEDED,
            provider_task_id=provider_task_id,
            execution_mode=ProviderExecutionMode.ASYNC,
            raw_response={"status": "succeeded", "race": "cancel_won_before_persist"},
        )

    def cancel(self, provider_task_id: str) -> None:
        _ = provider_task_id
        self.cancel_count += 1


class SubtitleTranscriberDouble:
    name = "asr-contract-double"
    model = "timestamp-double-v1"

    def __init__(self, mode: str) -> None:
        self.mode = mode

    def transcribe(self, audio_path: Path, *, duration_sec: Decimal | None = None) -> ASRTranscript:
        _ = duration_sec
        if self.mode == "error":
            raise ASRProviderError("asr_injected_error", "injected ASR failure", retryable=True)
        if self.mode == "pure_text_vad":
            if "region-01" in audio_path.name:
                text = "Where is the map"
            elif "region-02" in audio_path.name:
                text = "It is by the bridge"
            else:
                text = "Where is the map It is by the bridge"
            return ASRTranscript(text=text, language="en", tokens=(), response_format="json")
        return ASRTranscript(
            text="No reliable timestamps are available",
            language="en",
            tokens=(),
            response_format="json",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run isolated reliability benchmarks")
    parser.add_argument(
        "section",
        choices=("provider", "faults", "subtitle", "manifest", "reliability-all"),
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.section in {"provider", "reliability-all"}:
        run_provider_contract(output_dir)
    if args.section in {"faults", "reliability-all"}:
        run_fault_injection(output_dir)
    if args.section in {"subtitle", "reliability-all"}:
        run_subtitle_benchmark(output_dir)
    if args.section in {"manifest", "reliability-all"}:
        write_artifact_manifest(output_dir)
    return 0


def run_provider_contract(output_dir: Path) -> None:
    specs = [
        {"id": "async-queued-running-success-fetch", "submit": "queued", "polls": ["running", "succeeded"], "fetches": ["succeeded_asset"], "ops": ["submit", "poll", "poll", "fetch_result"], "expected": ["queued", "running", "succeeded", "succeeded"]},
        {"id": "async-queued-success-fetch", "submit": "queued", "polls": ["succeeded"], "fetches": ["succeeded_asset"], "ops": ["submit", "poll", "fetch_result"], "expected": ["queued", "succeeded", "succeeded"]},
        {"id": "async-running-success-fetch", "submit": "running", "polls": ["succeeded"], "fetches": ["succeeded_asset"], "ops": ["submit", "poll", "fetch_result"], "expected": ["running", "succeeded", "succeeded"]},
        {"id": "poll-timeout", "submit": "queued", "polls": ["timeout"], "ops": ["submit", "poll"], "expected": ["queued", "timeout"]},
        {"id": "poll-failed-accepted", "submit": "queued", "polls": ["failed_accepted"], "ops": ["submit", "poll"], "expected": ["queued", "failed"]},
        {"id": "submit-timeout-normalized", "submit": "failed_not_submitted", "ops": ["submit"], "expected": ["failed"]},
        {"id": "submit-result-unknown-normalized", "submit": "failed_unknown", "ops": ["submit"], "expected": ["failed"]},
        {"id": "submit-provider-cancelled", "submit": "cancelled", "ops": ["submit"], "expected": ["cancelled"]},
        {"id": "cancel-queued", "submit": "queued", "ops": ["submit", "cancel"], "expected": ["queued", "cancelled_by_client"]},
        {"id": "cancel-running", "submit": "running", "ops": ["submit", "cancel"], "expected": ["running", "cancelled_by_client"]},
        {"id": "fetch-running-then-success", "submit": "queued", "fetches": ["running", "succeeded_asset"], "ops": ["submit", "fetch_result", "fetch_result"], "expected": ["queued", "running", "succeeded"]},
        {"id": "fetch-result-missing-normalized", "submit": "queued", "fetches": ["fetch_missing"], "ops": ["submit", "fetch_result"], "expected": ["queued", "failed"]},
        {"id": "duplicate-result-stable", "submit": "queued", "fetches": ["succeeded_asset", "succeeded_asset"], "ops": ["submit", "fetch_result", "fetch_result"], "expected": ["queued", "succeeded", "succeeded"]},
        {"id": "malformed-response-normalized", "submit": "malformed_normalized", "ops": ["submit"], "expected": ["failed"]},
        {"id": "cancel-unsupported", "submit": "queued", "cancel_unsupported": True, "ops": ["submit", "cancel"], "expected": ["queued", "cancel_unsupported"]},
    ]
    rows = [_run_provider_contract_case(spec, index + 1) for index, spec in enumerate(specs)]
    rows.extend(_run_builtin_mock_contracts(start_index=len(rows) + 1))
    write_json(output_dir / "provider_contract.json", rows)
    write_csv(
        output_dir / "provider_contract.csv",
        [
            {
                "case_id": row["case_id"],
                "adapter": row["adapter"],
                "operations": row["input"]["operations"],
                "status_sequence": row["assertion_results"]["status_sequence"],
                "contract_passed": row["assertion_results"]["contract_passed"],
                "failure_reason": row["failure_reason"],
                "latency_sec": row["latency_sec"],
                "evidence_class": row["evidence_class"],
            }
            for row in rows
        ],
        fieldnames=["case_id", "adapter", "operations", "status_sequence", "contract_passed", "failure_reason", "latency_sec", "evidence_class"],
    )
    summary = {
        "case_count": len(rows),
        "contract_pass_rate": _rate(row["assertion_results"]["contract_passed"] for row in rows),
        "state_transition_correctness": _rate(
            row["assertion_results"].get("status_sequence_matches", True) for row in rows
        ),
        "unhandled_exception_rate": _rate(row["failure_reason"] is not None for row in rows),
        "invalid_terminal_state_rate": _rate(
            not row["assertion_results"].get("valid_terminal_outcome", True) for row in rows
        ),
        "operation_coverage": sorted({operation for row in rows for operation in row["input"]["operations"]}),
        "evidence_class_counts": dict(Counter(row["evidence_class"] for row in rows)),
        "real_external_provider_calls": 0,
        "result_sha256": content_hash(rows),
    }
    write_json(output_dir / "provider_contract_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)


def _run_provider_contract_case(spec: dict[str, Any], order: int) -> dict[str, Any]:
    provider = LifecycleDouble(spec)
    request = _provider_request(f"provider-contract-{order:02d}")
    statuses: list[str] = []
    outputs: list[dict[str, Any]] = []
    failure: dict[str, Any] | None = None
    started = time.perf_counter()
    provider_task_id = "contract-remote-1"
    try:
        for operation in spec["ops"]:
            if operation == "submit":
                response = provider.submit(request)
                provider_task_id = response.provider_task_id or provider_task_id
                statuses.append(response.status.value)
                outputs.append(_response_payload(response))
            elif operation == "poll":
                response = provider.poll(provider_task_id)
                statuses.append(response.status.value)
                outputs.append(_response_payload(response))
            elif operation == "fetch_result":
                response = provider.fetch_result(
                    provider_task_id,
                    request=request,
                    provider_context={"accepted": True},
                )
                statuses.append(response.status.value)
                outputs.append(_response_payload(response))
            elif operation == "cancel":
                try:
                    provider.cancel(provider_task_id)
                    statuses.append("cancelled_by_client")
                except NotImplementedError:
                    statuses.append("cancel_unsupported")
                except OSError:
                    statuses.append("cancel_transport_error")
    except Exception as exc:
        failure = {"type": type(exc).__name__, "message": str(exc)}

    stable_ids = all(
        not item.get("provider_task_id") or item["provider_task_id"] == provider_task_id
        for item in outputs
    )
    terminal_assets_valid = all(
        item["status"] != "succeeded"
        or operation != "fetch_result"
        or bool(item.get("assets"))
        for operation, item in zip(spec["ops"], outputs)
    )
    error_contract_valid = all(
        item["status"] != "failed"
        or bool(item.get("error", {}).get("error_code"))
        for item in outputs
    )
    valid_terminal_outcome = bool(statuses) and statuses[-1] in {
        "succeeded",
        "failed",
        "timeout",
        "cancelled",
        "cancelled_by_client",
        "cancel_unsupported",
        "cancel_transport_error",
    }
    assertions = {
        "status_sequence": statuses,
        "expected_status_sequence": spec["expected"],
        "status_sequence_matches": statuses == spec["expected"],
        "provider_task_id_stable": stable_ids,
        "terminal_fetch_asset_valid": terminal_assets_valid,
        "error_contract_valid": error_contract_valid,
        "valid_terminal_outcome": valid_terminal_outcome,
        "call_order_matches": [item["operation"] for item in provider.calls] == spec["ops"],
    }
    assertions["contract_passed"] = failure is None and all(
        value for key, value in assertions.items() if key not in {"status_sequence", "expected_status_sequence"}
    )
    return {
        "case_id": spec["id"],
        "dataset_order": order,
        "arm": "provider_lifecycle_contract",
        "adapter": provider.name,
        "input": {"operations": spec["ops"], "request": _request_payload(request)},
        "raw_output": {"calls": provider.calls, "responses": outputs},
        "parsed_output": {"status_sequence": statuses},
        "assertion_results": assertions,
        "failure_reason": failure,
        "model": provider.model,
        "provider": provider.name,
        "capability_version": "provider-adapter-contract-v1",
        "prompt_version": None,
        "latency_sec": round(time.perf_counter() - started, 6),
        "token_usage": None,
        "retry_count": 0,
        "evidence_class": "contract_double_no_external_call",
    }


def _run_builtin_mock_contracts(*, start_index: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for offset, provider in enumerate((MockLLMProvider(), MockImageProvider(), MockVideoProvider())):
        request = _provider_request(f"builtin-mock-{provider.type.value}", model=provider.model)
        if provider.type == ProviderType.LLM:
            request = ProviderRequest(**{**asdict(request), "metadata": {"stage": "provider_contract"}})
        started = time.perf_counter()
        failure: dict[str, Any] | None = None
        try:
            response = provider.submit(request)
            asset_valid = provider.type == ProviderType.LLM or bool(response.assets)
            passed = response.status == ProviderStatus.SUCCEEDED and bool(response.provider_task_id) and asset_valid
            raw = _response_payload(response)
        except Exception as exc:
            response = None
            passed = False
            raw = {}
            failure = {"type": type(exc).__name__, "message": str(exc)}
        rows.append(
            {
                "case_id": f"builtin-mock-{provider.type.value}-submit",
                "dataset_order": start_index + offset,
                "arm": "built_in_mock_adapter_contract",
                "adapter": provider.name,
                "input": {"operations": ["submit"], "request": _request_payload(request)},
                "raw_output": raw,
                "parsed_output": {"status_sequence": [response.status.value] if response else []},
                "assertion_results": {
                    "status_sequence": [response.status.value] if response else [],
                    "status_sequence_matches": bool(response and response.status == ProviderStatus.SUCCEEDED),
                    "provider_task_id_stable": bool(response and response.provider_task_id),
                    "terminal_fetch_asset_valid": asset_valid if response else False,
                    "error_contract_valid": True,
                    "valid_terminal_outcome": bool(response and response.status == ProviderStatus.SUCCEEDED),
                    "call_order_matches": True,
                    "contract_passed": passed,
                },
                "failure_reason": failure,
                "model": provider.model,
                "provider": provider.name,
                "capability_version": "built-in-mock-adapter@working-tree",
                "prompt_version": None,
                "latency_sec": round(time.perf_counter() - started, 6),
                "token_usage": None,
                "retry_count": 0,
                "evidence_class": "built_in_mock_adapter_no_external_call",
            }
        )
    return rows


def run_fault_injection(output_dir: Path) -> None:
    scenarios = (
        ("duplicate_submission", _fault_duplicate_submission),
        ("crash_before_submit", _fault_crash_before_submit),
        ("ack_lost", _fault_ack_lost),
        ("worker_crash_during_poll", _fault_worker_crash_during_poll),
        ("stale_worker_write", _fault_stale_worker_write),
        ("duplicate_completion", _fault_duplicate_completion),
        ("cancel_race", _fault_cancel_race),
    )
    rows: list[dict[str, Any]] = []
    for scenario, function in scenarios:
        for repetition in range(1, FAULT_REPETITIONS + 1):
            row = function(repetition)
            rows.append(row)
            print(
                f"[fault] {scenario} {repetition}/{FAULT_REPETITIONS} "
                f"{'pass' if row['assertion_results']['scenario_expectation_passed'] else 'exposed'}",
                flush=True,
            )
    write_json(output_dir / "fault_injection.json", rows)
    write_csv(
        output_dir / "fault_injection.csv",
        [_fault_csv_row(row) for row in rows],
        fieldnames=[
            "case_id", "scenario", "repetition", "scenario_expectation_passed",
            "business_recovery_succeeded", "safe_containment", "final_status",
            "provider_submit_count", "duplicate_provider_submissions",
            "duplicate_persisted_candidates", "duplicate_persisted_assets",
            "duplicate_terminal_writes", "stale_write_accepted", "idempotency_success",
            "false_success", "latency_sec", "failure_reason",
        ],
    )
    by_scenario: dict[str, Any] = {}
    for scenario, _function in scenarios:
        values = [row for row in rows if row["scenario"] == scenario]
        by_scenario[scenario] = {
            "case_count": len(values),
            "expectation_pass_rate": _rate(row["assertion_results"]["scenario_expectation_passed"] for row in values),
            "business_recovery_rate": _rate(row["assertion_results"]["business_recovery_succeeded"] for row in values),
            "safe_containment_rate": _rate(row["assertion_results"]["safe_containment"] for row in values),
            "p50_recovery_latency_sec": percentile([row["latency_sec"] for row in values], 0.50),
            "p95_recovery_latency_sec": percentile([row["latency_sec"] for row in values], 0.95),
        }
    latencies = [float(row["latency_sec"]) for row in rows]
    stale_worker_write_rejection_rate = _rate(
        not row["assertion_results"]["stale_write_accepted"]
        for row in rows
        if row["scenario"] == "stale_worker_write"
    )
    duplicate_completion_idempotency_rate = _rate(
        row["assertion_results"]["idempotency_success"]
        for row in rows
        if row["scenario"] == "duplicate_completion"
    )
    exposed_invariants: list[str] = []
    if stale_worker_write_rejection_rate < 1.0:
        exposed_invariants.append("terminal writes are not fenced by lease token")
    if duplicate_completion_idempotency_rate < 1.0:
        exposed_invariants.append("task completion persistence is not idempotent")

    summary = {
        "case_count": len(rows),
        "scenario_count": len(scenarios),
        "repetitions_per_scenario": FAULT_REPETITIONS,
        "recovery_success_rate": _rate(row["assertion_results"]["scenario_expectation_passed"] for row in rows),
        "business_recovery_success_rate": _rate(row["assertion_results"]["business_recovery_succeeded"] for row in rows),
        "safe_containment_rate": _rate(row["assertion_results"]["safe_containment"] for row in rows),
        "duplicate_provider_submission_rate": _rate(
            int(row["assertion_results"]["duplicate_provider_submissions"]) > 0 for row in rows
        ),
        "duplicate_resource_creation_rate": _rate(
            int(row["assertion_results"]["duplicate_persisted_candidates"]) > 0
            or int(row["assertion_results"]["duplicate_persisted_assets"]) > 0
            for row in rows
        ),
        "duplicate_terminal_write_rate": _rate(
            int(row["assertion_results"]["duplicate_terminal_writes"]) > 0 for row in rows
        ),
        "stale_worker_write_rejection_rate": stale_worker_write_rejection_rate,
        "duplicate_completion_idempotency_rate": duplicate_completion_idempotency_rate,
        "duplicate_completion_idempotency_count": sum(
            bool(row["assertion_results"]["idempotency_success"])
            for row in rows
            if row["scenario"] == "duplicate_completion"
        ),
        "idempotency_success_rate": _rate(
            row["assertion_results"]["idempotency_success"]
            for row in rows
            if row["scenario"] == "duplicate_submission"
        ),
        "idempotency_success_count": sum(
            bool(row["assertion_results"]["idempotency_success"])
            for row in rows
            if row["scenario"] == "duplicate_submission"
        ),
        "idempotency_attempt_count": sum(row["scenario"] == "duplicate_submission" for row in rows),
        "lost_task_rate": _rate(
            row["assertion_results"]["final_status"]
            in {TaskStatus.QUEUED.value, TaskStatus.RUNNING.value, TaskStatus.WAITING_PROVIDER.value}
            for row in rows
        ),
        "unhandled_exception_rate": 0.0,
        "duplicate_side_effect_count": sum(
            int(row["assertion_results"][key])
            for row in rows
            for key in ("duplicate_provider_submissions", "duplicate_persisted_candidates", "duplicate_persisted_assets")
        ),
        "false_success_count": sum(bool(row["assertion_results"]["false_success"]) for row in rows),
        "p50_recovery_latency_sec": percentile(latencies, 0.50),
        "p95_recovery_latency_sec": percentile(latencies, 0.95),
        "latency_definition": "fault injection to durable observed outcome; isolated DB setup excluded",
        "by_scenario": by_scenario,
        "known_exposed_invariants": exposed_invariants,
        "real_external_provider_calls": 0,
        "result_sha256": content_hash(rows),
    }
    write_json(output_dir / "fault_injection_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)


def _fault_duplicate_submission(repetition: int) -> dict[str, Any]:
    with _isolated_database(f"duplicate-{repetition}") as state:
        started = time.perf_counter()
        barrier = threading.Barrier(2)

        def create(index: int) -> dict[str, Any]:
            with state.factory() as db:
                barrier.wait()
                try:
                    result = TaskRepository().create(
                        db,
                        project_id=state.project_id,
                        task_type="fault-duplicate-submit",
                        resource_key="same-resource",
                        idempotency_key="same-request",
                        input_payload={"attempt": index},
                    )
                    db.commit()
                    return {
                        "outcome": "created" if result.created else "reused",
                        "task_id": result.task.id,
                    }
                except TaskConflictError as exc:
                    db.rollback()
                    return {"outcome": "conflict", "task_id": exc.task_id}

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(create, (1, 2)))
        provider_submit_count = 0
        with state.factory() as db:
            task_count = int(db.scalar(select(func.count(GenerationTask.id))) or 0)
            claimed = TaskRepository().claim_next(
                db,
                task_types=("fault-duplicate-submit",),
                owner="worker-a",
                lease_seconds=30,
                now=datetime.now(timezone.utc) + timedelta(milliseconds=10),
            )
            if claimed is not None:
                provider_submit_count += 1
                TaskRepository().finish(db, claimed, status=TaskStatus.SUCCEEDED, progress_label="submitted once")
            db.commit()
        same_task = len({item["task_id"] for item in outcomes}) == 1
        idempotency_success = (
            sorted(item["outcome"] for item in outcomes) == ["created", "reused"]
            and task_count == 1
            and same_task
            and provider_submit_count == 1
        )
        passed = idempotency_success
        assertions = _fault_assertions(
            expectation=passed,
            business=passed,
            containment=False,
            final_status="succeeded" if passed else "invalid",
            provider_submit_count=provider_submit_count,
            duplicate_provider_submissions=max(0, provider_submit_count - 1),
            idempotency_success=idempotency_success,
        )
        return _fault_record(
            "duplicate_submission", repetition, started,
            input_value={"parallel_requests": 2, "resource_key": "same-resource", "idempotency_key": "same-request"},
            raw={"outcomes": outcomes, "task_count": task_count, "provider_submit_count": provider_submit_count},
            assertions=assertions,
        )


def _fault_crash_before_submit(repetition: int) -> dict[str, Any]:
    with _isolated_database(f"pre-submit-{repetition}") as state:
        repository = TaskRepository()
        with state.factory() as db:
            task = repository.create(
                db,
                project_id=state.project_id,
                task_type="fault-pre-submit",
                resource_key="resource",
                input_payload={"frozen": True},
                max_retries=1,
            ).task
            db.commit()
            task_id = task.id
        now = datetime.now(timezone.utc) + timedelta(milliseconds=10)
        with state.factory() as db:
            repository.claim_next(db, task_types=("fault-pre-submit",), owner="worker-a", lease_seconds=30, now=now)
            db.commit()
        started = time.perf_counter()
        runtime = LocalTaskRuntime(session_factory=state.factory)
        runtime._handle_execution_error(
            task_id,
            "worker-a",
            TaskExecutionError(
                "worker_crash_before_submit",
                "injected before provider submit",
                retryable=True,
                submission_state=SubmissionState.NOT_SUBMITTED,
            ),
        )
        with state.factory() as db:
            recovered = repository.claim_next(
                db,
                task_types=("fault-pre-submit",),
                owner="worker-b",
                lease_seconds=30,
                now=now + timedelta(seconds=1),
            )
            if recovered is not None:
                repository.finish(db, recovered, status=TaskStatus.SUCCEEDED, progress_label="recovered")
            db.commit()
            final = db.get(GenerationTask, task_id)
            final_status = final.status
            retry_count = final.retry_count
        passed = final_status == TaskStatus.SUCCEEDED.value and retry_count == 1
        return _fault_record(
            "crash_before_submit", repetition, started,
            input_value={"submission_state": "not_submitted", "max_retries": 1},
            raw={"retry_count": retry_count, "reclaimed": recovered is not None},
            assertions=_fault_assertions(expectation=passed, business=passed, containment=False, final_status=final_status),
        )


def _fault_ack_lost(repetition: int) -> dict[str, Any]:
    with _isolated_database(f"ack-lost-{repetition}", video_task=True) as state:
        provider = AckLostVideoProvider()
        _claim_task(state, owner="worker-a")
        started = time.perf_counter()
        failure: dict[str, Any] | None = None
        with (
            patch("app.production.video_task_handler.SessionLocal", state.factory),
            patch("app.production.video_task_handler.provider_registry.get", return_value=provider),
        ):
            try:
                VideoCandidateTaskHandler().execute(state.task_id)
            except TaskExecutionError as exc:
                failure = {"code": exc.code, "message": exc.message, "submission_state": exc.submission_state.value}
                LocalTaskRuntime(session_factory=state.factory)._handle_execution_error(state.task_id, "worker-a", exc)
        with state.factory() as db:
            task = db.get(GenerationTask, state.task_id)
            final_status = task.status
            error_code = task.error_code
        passed = provider.submit_count == 1 and final_status == TaskStatus.FAILED.value and error_code == "provider_submission_uncertain"
        assertions = _fault_assertions(
            expectation=passed,
            business=False,
            containment=passed,
            final_status=final_status,
            provider_submit_count=provider.submit_count,
        )
        return _fault_record(
            "ack_lost", repetition, started,
            input_value={"remote_acceptance": "simulated", "ack": "timeout_before_task_id_persist"},
            raw={"error_code": error_code, "provider_submit_count": provider.submit_count},
            assertions=assertions,
            failure=failure,
        )


def _fault_worker_crash_during_poll(repetition: int) -> dict[str, Any]:
    with _isolated_database(f"poll-crash-{repetition}", video_task=True) as state:
        provider = PollRecoveryVideoProvider()
        with state.factory() as db:
            task = db.get(GenerationTask, state.task_id)
            task.provider_task_id = "remote-poll-1"
            task.status = TaskStatus.WAITING_PROVIDER.value
            task.raw_response = {"provider_accepted_at": datetime.now(timezone.utc).isoformat()}
            db.add(task)
            db.commit()
        started = time.perf_counter()
        with (
            patch("app.production.video_task_handler.SessionLocal", state.factory),
            patch("app.production.video_task_handler.provider_registry.get", return_value=provider),
        ):
            handler = VideoCandidateTaskHandler()
            handler.execute(state.task_id)
            handler.execute(state.task_id)
        with state.factory() as db:
            task = db.get(GenerationTask, state.task_id)
            final_status = task.status
            candidate_count = int(db.scalar(select(func.count(AssetCandidate.id)).where(AssetCandidate.source_task_id == state.task_id)) or 0)
        passed = final_status == TaskStatus.SUCCEEDED.value and provider.submit_count == 0 and provider.poll_count == 2 and candidate_count == 1
        assertions = _fault_assertions(
            expectation=passed,
            business=passed,
            containment=False,
            final_status=final_status,
            provider_submit_count=provider.submit_count,
        )
        return _fault_record(
            "worker_crash_during_poll", repetition, started,
            input_value={"provider_task_id_persisted": True, "first_poll": "OSError"},
            raw={"poll_count": provider.poll_count, "fetch_count": provider.fetch_count, "candidate_count": candidate_count},
            assertions=assertions,
        )


def _fault_stale_worker_write(repetition: int) -> dict[str, Any]:
    with _isolated_database(f"stale-{repetition}") as state:
        repository = TaskRepository()
        with state.factory() as db:
            task = repository.create(
                db,
                project_id=state.project_id,
                task_type="fault-stale-write",
                resource_key="resource",
                input_payload={},
            ).task
            db.commit()
            task_id = task.id
        now = datetime.now(timezone.utc) + timedelta(milliseconds=10)
        with state.factory() as db:
            repository.claim_next(db, task_types=("fault-stale-write",), owner="worker-a", lease_seconds=1, now=now)
            db.commit()
        stale_db = state.factory()
        try:
            stale_task = stale_db.get(GenerationTask, task_id)
            with state.factory() as db:
                takeover = repository.claim_next(
                    db,
                    task_types=("fault-stale-write",),
                    owner="worker-b",
                    lease_seconds=30,
                    now=now + timedelta(seconds=2),
                )
                takeover_token = takeover.lease_token if takeover is not None else None
                db.commit()
            started = time.perf_counter()
            stale_finish_accepted = repository.finish(
                stale_db,
                stale_task,
                status=TaskStatus.SUCCEEDED,
                progress_label="stale worker wrote",
            )
            stale_db.commit()
        finally:
            stale_db.close()
        with state.factory() as db:
            after_stale = db.get(GenerationTask, task_id)
            stale_rejected = (
                not stale_finish_accepted
                and after_stale is not None
                and after_stale.status == TaskStatus.RUNNING.value
                and after_stale.lease_owner == "worker-b"
                and after_stale.lease_token == takeover_token
            )
            takeover_completed = bool(
                after_stale is not None
                and takeover_token
                and repository.finish(
                    db,
                    after_stale,
                    status=TaskStatus.SUCCEEDED,
                    progress_label="current worker completed",
                    owner="worker-b",
                    lease_token=takeover_token,
                )
            )
            db.commit()
            final = db.get(GenerationTask, task_id)
            stale_accepted = bool(stale_finish_accepted)
            final_status = final.status
        passed = (
            stale_rejected
            and takeover_completed
            and final_status == TaskStatus.SUCCEEDED.value
        )
        assertions = _fault_assertions(
            expectation=passed,
            business=passed,
            containment=False,
            final_status=final_status,
            stale_write_accepted=stale_accepted,
            false_success=stale_accepted,
        )
        return _fault_record(
            "stale_worker_write", repetition, started,
            input_value={"lease_owner_before": "worker-a", "lease_owner_after_takeover": "worker-b", "fencing_token": True},
            raw={
                "takeover_succeeded": takeover is not None,
                "stale_write_accepted": stale_accepted,
                "stale_write_rejected": stale_rejected,
                "current_worker_completed": takeover_completed,
            },
            assertions=assertions,
            failure={"code": "stale_terminal_write_not_fenced", "message": "old worker overwrote the newer lease"} if stale_accepted else None,
        )


def _fault_duplicate_completion(repetition: int) -> dict[str, Any]:
    with _isolated_database(f"duplicate-complete-{repetition}", video_task=True) as state:
        response = _successful_video_response("remote-complete-1")
        started = time.perf_counter()
        barrier = threading.Barrier(2)

        def deliver(_attempt: int) -> str | None:
            try:
                with state.factory() as db:
                    task = db.get(GenerationTask, state.task_id)
                    barrier.wait()
                    persist_video_candidate_task_result(db, task, response)
            except Exception as exc:
                return f"{type(exc).__name__}: {exc}"
            return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            errors = [
                error
                for error in executor.map(deliver, (1, 2))
                if error is not None
            ]
        with state.factory() as db:
            candidate_count = int(db.scalar(select(func.count(AssetCandidate.id)).where(AssetCandidate.source_task_id == state.task_id)) or 0)
            asset_count = int(db.scalar(select(func.count(Asset.id)).where(Asset.source_task_id == state.task_id)) or 0)
            final_status = db.get(GenerationTask, state.task_id).status
        duplicate_candidates = max(0, candidate_count - 1)
        duplicate_assets = max(0, asset_count - 1)
        invariant_held = candidate_count == 1 and asset_count == 1
        business_succeeded = (
            invariant_held
            and not errors
            and final_status == TaskStatus.SUCCEEDED.value
        )
        assertions = _fault_assertions(
            expectation=business_succeeded,
            business=business_succeeded,
            containment=False,
            final_status=final_status,
            duplicate_persisted_candidates=duplicate_candidates,
            duplicate_persisted_assets=duplicate_assets,
            duplicate_terminal_writes=1 if not invariant_held else 0,
            idempotency_success=invariant_held and not errors,
            false_success=final_status == TaskStatus.SUCCEEDED.value and not invariant_held,
        )
        return _fault_record(
            "duplicate_completion", repetition, started,
            input_value={"parallel_identical_completion_deliveries": 2, "source_task_id": state.task_id},
            raw={"candidate_count": candidate_count, "asset_count": asset_count, "errors": errors},
            assertions=assertions,
            failure={"code": "completion_not_idempotent", "message": "duplicate candidate/asset rows persisted"} if not invariant_held else None,
        )


def _fault_cancel_race(repetition: int) -> dict[str, Any]:
    with _isolated_database(f"cancel-race-{repetition}", video_task=True) as state:
        with state.factory() as db:
            task = db.get(GenerationTask, state.task_id)
            task.provider_task_id = "remote-cancel-1"
            task.status = TaskStatus.RUNNING.value
            task.lease_owner = "worker-a"
            task.raw_response = {"provider_accepted_at": datetime.now(timezone.utc).isoformat()}
            db.add(task)
            db.commit()
        provider = CancelRaceVideoProvider(state.factory, state.task_id)
        started = time.perf_counter()
        with (
            patch("app.production.video_task_handler.SessionLocal", state.factory),
            patch("app.production.video_task_handler.provider_registry.get", return_value=provider),
        ):
            VideoCandidateTaskHandler().execute(state.task_id)
            LocalTaskRuntime(session_factory=state.factory)._normalize_handler_checkpoint(state.task_id, "worker-a")
        with state.factory() as db:
            task = db.get(GenerationTask, state.task_id)
            final_status = task.status
            candidate_count = int(db.scalar(select(func.count(AssetCandidate.id)).where(AssetCandidate.source_task_id == state.task_id)) or 0)
        passed = final_status == TaskStatus.CANCELLED.value and candidate_count == 0 and provider.cancel_count == 1
        assertions = _fault_assertions(
            expectation=passed,
            business=False,
            containment=passed,
            final_status=final_status,
            provider_submit_count=provider.submit_count,
        )
        return _fault_record(
            "cancel_race", repetition, started,
            input_value={"poll_result": "succeeded", "cancel_injected": "during_poll_before_persistence"},
            raw={"remote_cancel_count": provider.cancel_count, "candidate_count": candidate_count},
            assertions=assertions,
        )


def run_subtitle_benchmark(output_dir: Path) -> None:
    cases = _subtitle_cases()
    rows: list[dict[str, Any]] = []
    absolute_errors: list[float] = []
    for index, case in enumerate(cases, start=1):
        started = time.perf_counter()
        aligned = align_dialogues_to_tokens(
            case["dialogues"],
            case["tokens"],
            clip_duration=Decimal(str(case["clip_duration_sec"])),
            min_match_score=0.55,
        )
        expected_ids = set(case["expected_aligned_ids"])
        actual_ids = set(aligned)
        order = [dialogue["id"] for dialogue in case["dialogues"] if dialogue["id"] in actual_ids]
        chronological = all(
            aligned[left].end_sec < aligned[right].start_sec
            for left, right in zip(order, order[1:])
        )
        cue_errors: list[float] = []
        for dialogue_id, gold in case.get("gold_timings", {}).items():
            if dialogue_id not in aligned:
                continue
            cue_errors.extend(
                [
                    abs(float(aligned[dialogue_id].start_sec) - float(gold[0])),
                    abs(float(aligned[dialogue_id].end_sec) - float(gold[1])),
                ]
            )
        absolute_errors.extend(cue_errors)
        passed = actual_ids == expected_ids and chronological
        rows.append(
            {
                "case_id": case["case_id"],
                "dataset_order": index,
                "arm": "deterministic_timestamp_alignment",
                "input": {
                    "dialogues": case["dialogues"],
                    "tokens": [asdict(token) for token in case["tokens"]],
                    "clip_duration_sec": case["clip_duration_sec"],
                    "expected_aligned_ids": case["expected_aligned_ids"],
                },
                "raw_output": None,
                "parsed_output": {key: asdict(value) for key, value in aligned.items()},
                "assertion_results": {
                    "passed": passed,
                    "pipeline_completed": True,
                    "aligned_count": len(aligned),
                    "target_count": len(case["dialogues"]),
                    "fallback_count": len(case["dialogues"]) - len(aligned),
                    "order_correct": chronological,
                    "valid_timeline": chronological,
                    "alignment_decision_correct_count": (
                        len(case["dialogues"]) if passed else 0
                    ),
                    "start_end_absolute_errors_sec": cue_errors,
                },
                "failure_reason": None if passed else {"code": "subtitle_alignment_expectation_mismatch"},
                "model": "deterministic-token-alignment",
                "provider": "timestamp-double",
                "capability_version": "asr-vad-v1",
                "prompt_version": None,
                "latency_sec": round(time.perf_counter() - started, 6),
                "token_usage": None,
                "retry_count": 0,
                "evidence_class": "deterministic_asr_timestamp_double_no_external_call",
            }
        )
    rows.extend(_run_full_subtitle_fallback_cases(start_index=len(rows) + 1))
    absolute_errors = [
        float(value)
        for row in rows
        for value in row["assertion_results"].get("start_end_absolute_errors_sec") or []
    ]
    target_count = sum(row["assertion_results"]["target_count"] for row in rows)
    aligned_count = sum(row["assertion_results"]["aligned_count"] for row in rows)
    summary = {
        "case_count": len(rows),
        "target_dialogue_count": target_count,
        "alignment_case_pass_rate": _rate(row["assertion_results"]["passed"] for row in rows),
        "subtitle_pipeline_completion_rate": _rate(
            row["assertion_results"].get("pipeline_completed", True) for row in rows
        ),
        "dialogue_alignment_rate": aligned_count / target_count if target_count else 1.0,
        "dialogue_alignment_expectation_accuracy": (
            sum(
                int(row["assertion_results"].get("alignment_decision_correct_count") or 0)
                for row in rows
            )
            / target_count
            if target_count
            else 1.0
        ),
        "fallback_rate": (target_count - aligned_count) / target_count if target_count else 0.0,
        "valid_timeline_rate": _rate(
            row["assertion_results"].get("valid_timeline", row["assertion_results"].get("order_correct"))
            for row in rows
        ),
        "missing_dialogue_after_alignment_or_declared_fallback_rate": 0.0,
        "subtitle_start_end_mae_sec": sum(absolute_errors) / len(absolute_errors) if absolute_errors else None,
        "mae_sample_count": len(absolute_errors),
        "real_asr_calls": 0,
        "result_sha256": content_hash(rows),
    }
    write_json(output_dir / "subtitle_eval.json", rows)
    write_csv(
        output_dir / "subtitle_eval.csv",
        [
            {
                "case_id": row["case_id"],
                "passed": row["assertion_results"]["passed"],
                "aligned_count": row["assertion_results"]["aligned_count"],
                "target_count": row["assertion_results"]["target_count"],
                "fallback_count": row["assertion_results"]["fallback_count"],
                "order_correct": row["assertion_results"]["order_correct"],
                "latency_sec": row["latency_sec"],
            }
            for row in rows
        ],
        fieldnames=["case_id", "passed", "aligned_count", "target_count", "fallback_count", "order_correct", "latency_sec"],
    )
    write_json(output_dir / "subtitle_eval_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)


def _run_full_subtitle_fallback_cases(*, start_index: int) -> list[dict[str, Any]]:
    specs = [
        {
            "case_id": "pure-text-vad-fallback",
            "mode": "pure_text_vad",
            "silence_output": (
                "silence_start: 0\nsilence_end: 0.2\n"
                "silence_start: 3.0\nsilence_end: 3.4\n"
                "silence_start: 6.8\nsilence_end: 8.0\n"
            ),
            "dialogues": [("d1", "Where is the map?"), ("d2", "It is by the bridge.")],
            "expected_aligned": 2,
            "expected_fallback": 0,
            "expected_status": "succeeded",
        },
        {
            "case_id": "asr-error-deterministic-fallback",
            "mode": "error",
            "silence_output": "",
            "dialogues": [("d1", "Please open the box.")],
            "expected_aligned": 0,
            "expected_fallback": 1,
            "expected_status": "failed",
        },
        {
            "case_id": "silent-gap-unavailable-fallback",
            "mode": "silent_gap",
            "silence_output": "",
            "dialogues": [("d1", "I can hear you.")],
            "expected_aligned": 0,
            "expected_fallback": 1,
            "expected_status": "failed",
        },
    ]
    rows: list[dict[str, Any]] = []
    for offset, spec in enumerate(specs):
        dialogues = [
            {
                "id": dialogue_id,
                "shot_id": "shot-1",
                "sequence_order": index,
                "start_time": None,
                "text": text,
            }
            for index, (dialogue_id, text) in enumerate(spec["dialogues"])
        ]
        snapshot = {
            "subtitle_mode": "english",
            "shots": [{"id": "shot-1", "shot_no": 1, "duration_sec": "8", "shot_card": {}}],
            "video_assets": [{"id": "asset-1", "uri": "/tmp/benchmark-source.mp4"}],
            "dialogues": dialogues,
        }

        def fake_runner(args: list[str], **_kwargs: Any) -> MediaProcessResult:
            if any("silencedetect=" in str(value) for value in args):
                return MediaProcessResult(returncode=0, output_tail=str(spec["silence_output"]))
            output_path = Path(args[-1])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"wav-double")
            return MediaProcessResult(returncode=0, output_tail="")

        started = time.perf_counter()
        failure: dict[str, Any] | None = None
        try:
            with tempfile.TemporaryDirectory(prefix="verbascene-subtitle-fallback-") as temp_dir:
                result = align_export_subtitles(
                    snapshot,
                    Path(temp_dir),
                    cancel_requested=lambda: False,
                    transcriber=SubtitleTranscriberDouble(str(spec["mode"])),
                    media_runner=fake_runner,
                )
            valid_timeline = _subtitle_timings_valid(result.timings, duration=Decimal("8"))
            passed = (
                result.status == spec["expected_status"]
                and result.aligned_count == spec["expected_aligned"]
                and result.fallback_count == spec["expected_fallback"]
                and valid_timeline
            )
            manifest = result.to_manifest()
            parsed = {key: asdict(value) for key, value in result.timings.items()}
        except Exception as exc:
            passed = False
            valid_timeline = False
            manifest = {}
            parsed = {}
            failure = {"type": type(exc).__name__, "message": str(exc)}
        target_count = len(dialogues)
        aligned_count = int(manifest.get("aligned_count") or 0)
        fallback_count = int(manifest.get("fallback_count") or 0)
        rows.append(
            {
                "case_id": spec["case_id"],
                "dataset_order": start_index + offset,
                "arm": "asr_vad_to_deterministic_fallback",
                "input": {"snapshot": snapshot, "fault_mode": spec["mode"]},
                "raw_output": manifest,
                "parsed_output": parsed,
                "assertion_results": {
                    "passed": passed,
                    "pipeline_completed": failure is None,
                    "aligned_count": aligned_count,
                    "target_count": target_count,
                    "fallback_count": fallback_count,
                    "order_correct": valid_timeline,
                    "valid_timeline": valid_timeline,
                    "alignment_decision_correct_count": target_count if passed else 0,
                    "start_end_absolute_errors_sec": [],
                },
                "failure_reason": failure,
                "model": "timestamp-double-v1",
                "provider": "asr-contract-double",
                "capability_version": "asr-vad-v1",
                "prompt_version": None,
                "latency_sec": round(time.perf_counter() - started, 6),
                "token_usage": None,
                "retry_count": 0,
                "evidence_class": "asr_vad_contract_double_no_external_call",
            }
        )
    return rows


def _subtitle_timings_valid(timings: dict[str, Any], *, duration: Decimal) -> bool:
    ordered = sorted(timings.values(), key=lambda item: (item.start_sec, item.end_sec))
    if any(item.start_sec < 0 or item.end_sec <= item.start_sec or item.end_sec > duration for item in ordered):
        return False
    return all(left.end_sec < right.start_sec for left, right in zip(ordered, ordered[1:]))


def write_artifact_manifest(output_dir: Path) -> None:
    files: list[dict[str, Any]] = []
    for path in sorted(item for item in output_dir.rglob("*") if item.is_file() and item.name != "artifact_manifest.json"):
        relative = path.relative_to(output_dir).as_posix()
        files.append(
            {
                "path": relative,
                "sha256": _sha256_file(path),
                "bytes": path.stat().st_size,
                "evidence_class": _artifact_evidence_class(relative),
            }
        )
    manifest = {
        "benchmark_version": BENCHMARK_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "artifact_count": len(files),
        "artifacts": files,
    }
    manifest["manifest_content_sha256"] = content_hash(files)
    write_json(output_dir / "artifact_manifest.json", manifest)
    print(json.dumps({"artifact_count": len(files), "manifest_content_sha256": manifest["manifest_content_sha256"]}), flush=True)


@contextmanager
def _isolated_database(label: str, *, video_task: bool = False) -> Iterator[Any]:
    with tempfile.TemporaryDirectory(prefix=f"verbascene-{label}-") as temp_dir:
        root = Path(temp_dir)
        engine = create_database_engine(f"sqlite+pysqlite:///{(root / 'benchmark.sqlite3').as_posix()}")
        initialize_database(engine)
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        with factory() as db:
            project = Project(title=f"Fault {label}", style="benchmark")
            db.add(project)
            db.flush()
            shot_id: str | None = None
            task_id: str | None = None
            if video_task:
                shot = Shot(
                    project_id=project.id,
                    shot_no=1,
                    description="Benchmark shot",
                    video_prompt="Mia waves.",
                    duration_sec=Decimal("4"),
                    status="approved",
                )
                db.add(shot)
                db.flush()
                shot_id = shot.id
                task = TaskRepository().create(
                    db,
                    project_id=project.id,
                    task_type=VIDEO_CANDIDATE_TASK_TYPE,
                    resource_key=f"shot:{shot.id}:video",
                    provider="fault-double",
                    model="fault-double-v1",
                    input_payload={
                        "shot_id": shot.id,
                        "provider_request": {
                            "model": "fault-double-v1",
                            "prompt": "Mia waves.",
                            "negative_prompt": "text, watermark",
                            "references": [],
                            "params": {"duration_sec": 4, "resolution": "854x480"},
                            "metadata": {"benchmark": True},
                        },
                        "request_metadata": {"duration_mode": "fixed", "duration_sec": "4"},
                    },
                ).task
                db.flush()
                task_id = task.id
            db.commit()
            project_id = project.id
        state = type(
            "IsolatedState",
            (),
            {
                "factory": factory,
                "project_id": project_id,
                "shot_id": shot_id,
                "task_id": task_id,
                "storage_root": root / "storage",
            },
        )()
        try:
            yield state
        finally:
            engine.dispose()


def _claim_task(state: Any, *, owner: str) -> None:
    with state.factory() as db:
        claimed = TaskRepository().claim_next(
            db,
            task_types=(VIDEO_CANDIDATE_TASK_TYPE,),
            owner=owner,
            lease_seconds=30,
            now=datetime.now(timezone.utc) + timedelta(milliseconds=10),
        )
        db.commit()
        if claimed is None:
            raise AssertionError("fault fixture task was not claimable")


def _fault_assertions(
    *,
    expectation: bool,
    business: bool,
    containment: bool,
    final_status: str,
    provider_submit_count: int = 0,
    duplicate_provider_submissions: int = 0,
    duplicate_persisted_candidates: int = 0,
    duplicate_persisted_assets: int = 0,
    duplicate_terminal_writes: int = 0,
    stale_write_accepted: bool = False,
    idempotency_success: bool = False,
    false_success: bool = False,
) -> dict[str, Any]:
    return {
        "scenario_expectation_passed": expectation,
        "business_recovery_succeeded": business,
        "safe_containment": containment,
        "final_status": final_status,
        "provider_submit_count": provider_submit_count,
        "duplicate_provider_submissions": duplicate_provider_submissions,
        "duplicate_persisted_candidates": duplicate_persisted_candidates,
        "duplicate_persisted_assets": duplicate_persisted_assets,
        "duplicate_terminal_writes": duplicate_terminal_writes,
        "stale_write_accepted": stale_write_accepted,
        "idempotency_success": idempotency_success,
        "false_success": false_success,
    }


def _fault_record(
    scenario: str,
    repetition: int,
    started: float,
    *,
    input_value: dict[str, Any],
    raw: dict[str, Any],
    assertions: dict[str, Any],
    failure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "case_id": f"{scenario}-{repetition:02d}",
        "scenario": scenario,
        "repetition": repetition,
        "arm": "fault_injection_current_implementation",
        "input": input_value,
        "raw_output": raw,
        "parsed_output": {"final_status": assertions["final_status"]},
        "assertion_results": assertions,
        "failure_reason": failure,
        "model": "fault-double-v1",
        "provider": "isolated-runtime-and-provider-double",
        "capability_version": "task-runtime@working-tree",
        "prompt_version": None,
        "latency_sec": round(time.perf_counter() - started, 6),
        "token_usage": None,
        "retry_count": int(raw.get("retry_count") or 0),
        "evidence_class": "isolated_sqlite_fault_injection_with_provider_double",
    }


def _fault_csv_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_id": row["case_id"],
        "scenario": row["scenario"],
        "repetition": row["repetition"],
        **row["assertion_results"],
        "latency_sec": row["latency_sec"],
        "failure_reason": row["failure_reason"],
    }


def _successful_video_response(provider_task_id: str) -> ProviderResponse:
    return ProviderResponse(
        status=ProviderStatus.SUCCEEDED,
        provider_task_id=provider_task_id,
        execution_mode=ProviderExecutionMode.ASYNC,
        assets=[
            ProviderAsset(
                asset_type="video",
                uri=f"mock://benchmark/{provider_task_id}.mp4",
                mime_type="video/mp4",
                width=854,
                height=480,
                duration_sec=Decimal("4"),
                metadata={"temporary_file": False},
            )
        ],
        raw_response={"status": "succeeded", "contract_double": True},
    )


def _contract_response(kind: str, provider_task_id: str) -> ProviderResponse:
    status_by_kind = {
        "queued": ProviderStatus.QUEUED,
        "running": ProviderStatus.RUNNING,
        "succeeded": ProviderStatus.SUCCEEDED,
        "succeeded_asset": ProviderStatus.SUCCEEDED,
        "timeout": ProviderStatus.TIMEOUT,
        "cancelled": ProviderStatus.CANCELLED,
        "failed_not_submitted": ProviderStatus.FAILED,
        "failed_unknown": ProviderStatus.FAILED,
        "failed_accepted": ProviderStatus.FAILED,
        "fetch_missing": ProviderStatus.FAILED,
        "malformed_normalized": ProviderStatus.FAILED,
    }
    status = status_by_kind[kind]
    error: ProviderError | None = None
    if kind.startswith("failed") or kind in {"fetch_missing", "malformed_normalized"}:
        submission = {
            "failed_not_submitted": SubmissionState.NOT_SUBMITTED,
            "failed_unknown": SubmissionState.UNKNOWN,
            "failed_accepted": SubmissionState.ACCEPTED,
            "fetch_missing": SubmissionState.ACCEPTED,
            "malformed_normalized": SubmissionState.UNKNOWN,
        }[kind]
        error = ProviderError(
            error_code=kind,
            error_message=f"injected {kind}",
            is_retryable=kind != "failed_accepted",
            submission_state=submission,
        )
    return ProviderResponse(
        status=status,
        provider_task_id=provider_task_id,
        execution_mode=ProviderExecutionMode.ASYNC,
        poll_after_sec=1 if status in {ProviderStatus.QUEUED, ProviderStatus.RUNNING} else None,
        assets=_successful_video_response(provider_task_id).assets if kind == "succeeded_asset" else [],
        raw_response={"kind": kind},
        error=error,
    )


def _provider_request(task_id: str, *, model: str = "contract-double-v1") -> ProviderRequest:
    return ProviderRequest(
        project_id="provider-contract-project",
        task_id=task_id,
        model=model,
        prompt="A character waves and says hello.",
        params={"duration_sec": 4, "resolution": "854x480"},
        metadata={"benchmark_version": BENCHMARK_VERSION},
    )


def _request_payload(request: ProviderRequest) -> dict[str, Any]:
    return asdict(request)


def _response_payload(response: ProviderResponse) -> dict[str, Any]:
    return asdict(response)


def _subtitle_cases() -> list[dict[str, Any]]:
    one = _timed_words("Hello Mia", start=Decimal("0.5"))
    two = _timed_words("Can you help me Yes I can", start=Decimal("0.4"))
    punct = _timed_words("Wait please Here you are", start=Decimal("0.6"))
    fuzzy = _timed_words("I can see the colour blue", start=Decimal("0.3"))
    partial = _timed_words("The map is here", start=Decimal("0.8"))
    return [
        _subtitle_case("exact-single", [("d1", "Hello, Mia!")], one, ["d1"], {"d1": (_cue_start(one, 0), _cue_end(one, len(one) - 1))}),
        _subtitle_case("two-dialogues-monotonic", [("d1", "Can you help me?"), ("d2", "Yes, I can.")], two, ["d1", "d2"], {"d1": (_cue_start(two, 0), _cue_end(two, 3)), "d2": (_cue_start(two, 4), _cue_end(two, 6))}),
        _subtitle_case("punctuation-normalization", [("d1", "Wait, please."), ("d2", "Here you are!")], punct, ["d1", "d2"], {"d1": (_cue_start(punct, 0), _cue_end(punct, 1)), "d2": (_cue_start(punct, 2), _cue_end(punct, 4))}),
        _subtitle_case("minor-asr-spelling-variation", [("d1", "I can see the color blue.")], fuzzy, ["d1"], {}),
        _subtitle_case("low-confidence-fallback", [("d1", "Slow is fine.")], _timed_words("completely different words", start=Decimal("0.5")), [], {}),
        _subtitle_case("empty-asr-fallback", [("d1", "Where is the ball?")], [], [], {}),
        _subtitle_case("partial-match-fallback", [("d1", "The map is here."), ("d2", "The bridge is there.")], partial, ["d1"], {"d1": (_cue_start(partial, 0), _cue_end(partial, 3))}),
    ]


def _subtitle_case(
    case_id: str,
    dialogue_values: list[tuple[str, str]],
    tokens: list[ASRTimedToken],
    expected_ids: list[str],
    gold: dict[str, tuple[Decimal, Decimal]],
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "dialogues": [
            {"id": dialogue_id, "sequence_order": index, "text": text}
            for index, (dialogue_id, text) in enumerate(dialogue_values)
        ],
        "tokens": tokens,
        "expected_aligned_ids": expected_ids,
        "gold_timings": gold,
        "clip_duration_sec": 12,
    }


def _timed_words(text: str, *, start: Decimal) -> list[ASRTimedToken]:
    return [
        ASRTimedToken(
            text=word,
            start_sec=start + Decimal(index) * Decimal("0.45"),
            end_sec=start + Decimal(index) * Decimal("0.45") + Decimal("0.30"),
        )
        for index, word in enumerate(text.split())
    ]


def _cue_start(tokens: list[ASRTimedToken], index: int) -> Decimal:
    return max(Decimal("0"), tokens[index].start_sec - Decimal("0.10"))


def _cue_end(tokens: list[ASRTimedToken], index: int) -> Decimal:
    return tokens[index].end_sec + Decimal("0.20")


def _artifact_evidence_class(relative: str) -> str:
    if relative.startswith("_work/existing31") or relative.startswith("_work/reflexion") or relative.startswith("_work/bounded_patch"):
        return "live_real_llm_raw_case"
    if relative.startswith("existing_31") or relative.startswith("reflexion_ab") or relative.startswith("bounded_patch") or relative.startswith("pipeline_eval") or relative == "storyboard_dataset.json":
        return "live_real_llm_or_derived_evaluation"
    if relative.startswith("provider_contract"):
        return "contract_double_or_builtin_mock"
    if relative.startswith("fault_injection"):
        return "isolated_sqlite_fault_injection"
    if relative.startswith("subtitle_eval"):
        return "deterministic_timestamp_double"
    return "metadata_or_report"


def _rate(values: Any) -> float:
    items = [bool(value) for value in values]
    return sum(items) / len(items) if items else 0.0


def _sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
