from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import platform
from importlib.metadata import PackageNotFoundError, version as package_version
import subprocess
import sys
import time
from typing import Any, Callable


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parents[1]
sys.path.insert(0, str(API_ROOT))

# Application settings use a relative env file in normal API startup. A benchmark
# may be launched from the repository root, so anchor the same file explicitly.
from dotenv import load_dotenv  # noqa: E402

load_dotenv(API_ROOT / ".env", override=False)

from app.agents.script_contracts import SCRIPT_QUALITY_PIPELINE_VERSION  # noqa: E402
from app.agents.script_pipeline import (  # noqa: E402
    ScriptPipelineFailure,
    run_script_pipeline,
)
from app.agents.script_prompts import script_pipeline_system_prompt  # noqa: E402
from app.evals.resume_benchmark import (  # noqa: E402
    BENCHMARK_VERSION,
    build_bounded_patch_cases,
    build_extra_storyboard_cases,
    compact_checkpoint_raw,
    content_hash,
    evaluate_bounded_patch,
    evaluate_storyboard,
    json_default,
    percentile,
    phase_token_usage,
    script_input_from_eval_case,
    storyboard_case_from_script_result,
    write_csv,
    write_json,
)
from app.evals.script_quality import (  # noqa: E402
    DEFAULT_CASES_PATH,
    evaluate_script_output,
    load_script_quality_cases,
)
from app.providers.base import ProviderAdapter  # noqa: E402
from app.providers.defaults import provider_registry  # noqa: E402
from app.providers.types import (  # noqa: E402
    ProviderRequest,
    ProviderResponse,
    ProviderStatus,
    ProviderType,
)
from app.production.shot_direction.contracts import (  # noqa: E402
    SHOT_DIRECTION_PIPELINE_VERSION,
    ShotDirectionInput,
    build_shot_contract_report,
    parse_and_apply_shot_patch,
)
from app.production.shot_direction.pipeline import (  # noqa: E402
    ShotDirectionPipelineFailure,
    run_shot_direction_pipeline,
)
from app.production.shot_direction.prompts import (  # noqa: E402
    build_shot_patch_prompt,
    shot_direction_system_prompt,
)
from app.core.config import settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import GenerationTask  # noqa: E402
from sqlalchemy import select  # noqa: E402


SCRIPT_TEMPERATURES = {
    "story_blueprint": 0.75,
    "script_draft": 0.65,
    "script_review": 0.2,
    "script_patch": 0.35,
    "structure_recovery": 0.1,
}
STORYBOARD_TEMPERATURES = {
    "storyboard_draft": 0.4,
    "storyboard_reflection": 0.2,
    "storyboard_patch": 0.3,
    "structure_recovery": 0.1,
}
PIPELINE_EVAL_CASE_COUNT = 30
SCRIPT_PROMPT_PATH = API_ROOT / "app" / "agents" / "script_prompts.py"
SHOT_PROMPT_PATH = API_ROOT / "app" / "production" / "shot_direction" / "prompts.py"


class RecordingProvider(ProviderAdapter):
    """Per-case timing proxy; it never changes the wrapped adapter response."""

    def __init__(self, wrapped: ProviderAdapter) -> None:
        self.wrapped = wrapped
        self.name = wrapped.name
        self.type = wrapped.type
        self.model = wrapped.model
        self.capabilities = list(wrapped.capabilities)
        self.calls: list[dict[str, Any]] = []

    def submit(self, request: ProviderRequest) -> ProviderResponse:
        started = time.perf_counter()
        response = self.wrapped.submit(request)
        latency = time.perf_counter() - started
        self.calls.append(
            {
                "phase": str(request.metadata.get("phase") or request.metadata.get("stage") or "submit"),
                "task_id": request.task_id,
                "latency_sec": round(latency, 6),
                "status": response.status.value,
                "provider_task_id": response.provider_task_id,
            }
        )
        return response

    def poll(self, provider_task_id: str) -> ProviderResponse:
        return self.wrapped.poll(provider_task_id)

    def fetch_result(self, provider_task_id: str, *, request=None, provider_context=None):
        return self.wrapped.fetch_result(
            provider_task_id,
            request=request,
            provider_context=provider_context,
        )

    def cancel(self, provider_task_id: str) -> None:
        return self.wrapped.cancel(provider_task_id)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the evidence-preserving VerbaScene resume benchmark")
    parser.add_argument(
        "section",
        choices=(
            "environment",
            "scripts",
            "prepare-storyboard",
            "reflexion",
            "bounded-patch",
            "pipeline",
            "quality-all",
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=API_ROOT / "outputs" / f"verba_scene_resume_benchmark_{datetime.now():%Y%m%d}",
    )
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.section == "environment":
        write_environment(output_dir)
    elif args.section == "scripts":
        run_existing_script_eval(output_dir, workers=args.workers, resume=args.resume)
    elif args.section == "prepare-storyboard":
        prepare_storyboard_dataset(output_dir)
    elif args.section == "reflexion":
        run_reflexion_ab(output_dir, workers=args.workers, resume=args.resume)
    elif args.section == "bounded-patch":
        run_bounded_patch(output_dir, workers=args.workers, resume=args.resume)
    elif args.section == "pipeline":
        aggregate_pipeline_eval(output_dir)
    else:
        write_environment(output_dir)
        run_existing_script_eval(output_dir, workers=args.workers, resume=args.resume)
        prepare_storyboard_dataset(output_dir)
        run_reflexion_ab(output_dir, workers=args.workers, resume=args.resume)
        run_bounded_patch(output_dir, workers=args.workers, resume=args.resume)
        aggregate_pipeline_eval(output_dir)
    return 0


def write_environment(output_dir: Path) -> None:
    diff = _git(["diff", "--binary"])
    status_lines = [line for line in _git(["status", "--short"]).splitlines() if line]
    existing_cases = load_script_quality_cases()
    benchmark_sources = [
        API_ROOT / "app" / "evals" / "resume_benchmark.py",
        API_ROOT / "scripts" / "run_resume_benchmark.py",
        API_ROOT / "scripts" / "run_resume_reliability_benchmark.py",
    ]
    environment = {
        "benchmark_version": BENCHMARK_VERSION,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "repository_root": str(REPO_ROOT),
        "commit_sha": _git(["rev-parse", "HEAD"]),
        "branch": _git(["branch", "--show-current"]),
        "working_tree": {
            "clean": not status_lines,
            "changed_paths": status_lines,
            "tracked_diff_sha256": _sha256_text(diff),
        },
        "runtime": {
            "python": sys.version,
            "platform": platform.platform(),
            "process_id": os.getpid(),
            "dependencies": {
                name: _package_version(name)
                for name in ("fastapi", "sqlalchemy", "pydantic", "pydantic-settings", "alembic")
            },
        },
        "quality_model": {
            "provider": settings.llm_provider,
            "model": settings.llm_model,
            "base_url_origin": _origin(settings.dashscope_base_url if settings.llm_provider == "dashscope" else settings.llm_base_url),
            "temperature_policy": (
                "provider_owned_fixed_sampling; requested temperatures are not sent"
                if settings.llm_model.strip().lower() in {"kimi-k3", "kimi/kimi-k3"}
                else "phase temperatures sent"
            ),
            "configured_phase_temperatures": {
                "script": SCRIPT_TEMPERATURES,
                "storyboard": STORYBOARD_TEMPERATURES,
            },
        },
        "capability_versions": {
            "script_pipeline": SCRIPT_QUALITY_PIPELINE_VERSION,
            "shot_direction_pipeline": SHOT_DIRECTION_PIPELINE_VERSION,
            "benchmark": BENCHMARK_VERSION,
        },
        "database": {
            "quality_benchmark": "none; immutable value objects call production pipeline directly",
            "contract_and_fault_injection": "isolated temporary SQLite per scenario",
            "application_configured_persistence": settings.persistence_mode,
        },
        "evidence_matrix": {
            "existing_31_eval": _llm_evidence_label("current pipeline result"),
            "reflexion_ab": _llm_evidence_label("A/B result; no image/video provider calls"),
            "bounded_patch": _llm_evidence_label("controlled gold-defect result"),
            "pipeline_eval": _llm_evidence_label("plus deterministic contracts; no media generation"),
            "provider_contract": "provider doubles and built-in mock adapters",
            "fault_injection": "isolated SQLite plus provider doubles",
            "subtitle_fallback": "deterministic doubles; no real ASR call",
        },
        "provider_call_policy": {
            "llm_real_calls": settings.llm_provider != "mock",
            "image_real_calls": False,
            "video_real_calls": False,
            "asr_real_calls": False,
        },
        "existing_eval": {
            "path": str(DEFAULT_CASES_PATH),
            "case_count": len(existing_cases),
            "sha256": _sha256_file(DEFAULT_CASES_PATH),
            "historical_raw_outputs_found": False,
        },
        "benchmark_harness": {
            "source_files": [str(path) for path in benchmark_sources if path.is_file()],
            "source_sha256": {
                str(path.relative_to(REPO_ROOT)): _sha256_file(path)
                for path in benchmark_sources
                if path.is_file()
            },
        },
    }
    write_json(output_dir / "environment.json", environment)
    write_json(output_dir / "existing_31_eval_audit.json", _existing_eval_audit(existing_cases))
    write_json(output_dir / "task_history_audit.json", _task_history_audit())
    print(f"environment written: {output_dir / 'environment.json'}", flush=True)


def run_existing_script_eval(output_dir: Path, *, workers: int, resume: bool) -> None:
    cases = load_script_quality_cases()
    work_dir = output_dir / "_work" / "existing31"
    work_dir.mkdir(parents=True, exist_ok=True)
    base_provider = provider_registry.get(ProviderType.LLM, settings.llm_provider)

    def run_one(pair: tuple[int, dict[str, Any]]) -> dict[str, Any]:
        index, case = pair
        case_id = str(case["id"])
        path = work_dir / f"{index:02d}-{case_id}.json"
        if resume and path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
        source = script_input_from_eval_case(case, index=index)
        provider = RecordingProvider(base_provider)
        started = time.perf_counter()
        checkpoint: dict[str, Any] = {}
        parsed: dict[str, Any] = {}
        failure: dict[str, Any] | None = None
        try:
            result = run_script_pipeline(
                provider=provider,
                review_provider=provider,
                source=source,
                task_id=f"{output_dir.name}:existing31:{case_id}",
                model=settings.llm_model,
                review_model=settings.llm_model,
                system_prompt=script_pipeline_system_prompt(),
                review_system_prompt=script_pipeline_system_prompt(),
                temperatures=SCRIPT_TEMPERATURES,
            )
            checkpoint = result.checkpoint
            parsed = {
                "blueprint": result.blueprint,
                "draft": result.draft,
                "review": result.review,
                "patch": result.patch,
                "final": result.final,
                "contract_report": result.contract_report,
            }
        except ScriptPipelineFailure as exc:
            checkpoint = exc.checkpoint
            parsed = {
                key: deepcopy(checkpoint.get(key))
                for key in ("blueprint", "draft", "review", "patch", "final", "contract_report")
                if checkpoint.get(key) is not None
            }
            failure = {
                "type": type(exc).__name__,
                "phase": exc.phase,
                "code": exc.code,
                "message": exc.message,
                "submission_state": exc.submission_state.value,
            }
        except Exception as exc:
            failure = {"type": type(exc).__name__, "message": str(exc)}

        legacy_evaluation: dict[str, Any]
        if isinstance(parsed.get("final"), dict):
            try:
                legacy_evaluation = evaluate_script_output(case, parsed["final"])
            except Exception as exc:
                legacy_evaluation = {
                    "case_id": case_id,
                    "passed": False,
                    "issues": [{"code": "EVALUATOR_ERROR", "detail": str(exc)}],
                }
        else:
            legacy_evaluation = {
                "case_id": case_id,
                "passed": False,
                "issues": [{"code": "PIPELINE_NO_FINAL", "detail": (failure or {}).get("message", "no final")}],
            }
        record = {
            "case_id": case_id,
            "dataset_order": index,
            "input": deepcopy(case),
            "arm": "current_multi_agent_script_pipeline",
            "raw_output": compact_checkpoint_raw(checkpoint),
            "parsed_output": parsed,
            "assertion_results": legacy_evaluation,
            "failure_reason": failure,
            "model": settings.llm_model,
            "provider": settings.llm_provider,
            "capability_version": SCRIPT_QUALITY_PIPELINE_VERSION,
            "prompt_version": f"script_prompts.py@sha256:{_sha256_file(SCRIPT_PROMPT_PATH)}",
            "latency_sec": round(time.perf_counter() - started, 6),
            "phase_calls": provider.calls,
            "token_usage": phase_token_usage(checkpoint),
            "retry_count": 0,
            "evidence_class": _llm_evidence_class(),
        }
        write_json(path, record)
        return record

    rows = _run_parallel(list(enumerate(cases, start=1)), run_one, workers=workers, label="existing31")
    rows.sort(key=lambda item: int(item["dataset_order"]))
    for row in rows:
        row["prompt_version"] = f"script_prompts.py@sha256:{_sha256_file(SCRIPT_PROMPT_PATH)}"
        write_json(
            work_dir / f"{int(row['dataset_order']):02d}-{_filename(row['case_id'])}.json",
            row,
        )
    write_json(output_dir / "existing_31_eval.json", rows)
    write_csv(
        output_dir / "existing_31_eval.csv",
        [_script_csv_row(item) for item in rows],
        fieldnames=[
            "case_id",
            "dataset_order",
            "pipeline_completed",
            "legacy_eval_passed",
            "quality_gate",
            "patch_applied",
            "must_fix_count",
            "failure_phase",
            "failure_code",
            "latency_sec",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "provider",
            "model",
        ],
    )
    summary = {
        "case_count": len(rows),
        "pipeline_completion_rate": _rate(item["failure_reason"] is None for item in rows),
        "legacy_eval_pass_rate": _rate(bool(item["assertion_results"].get("passed")) for item in rows),
        "failure_categories": dict(Counter(_failure_category(item) for item in rows if item["failure_reason"])),
        "dataset_sha256": _sha256_file(DEFAULT_CASES_PATH),
        "result_sha256": content_hash(rows),
        "classification": "Current Version Live Result; not Archived Reaggregation",
    }
    write_json(output_dir / "existing_31_eval_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)


def prepare_storyboard_dataset(output_dir: Path) -> None:
    script_path = output_dir / "existing_31_eval.json"
    if not script_path.is_file():
        raise FileNotFoundError("Run the scripts section first")
    script_rows = json.loads(script_path.read_text(encoding="utf-8"))
    by_id = {str(item["case_id"]): item for item in script_rows}
    cases = load_script_quality_cases()
    storyboard_cases = [
        storyboard_case_from_script_result(
            case,
            index=index,
            script_result=by_id.get(str(case["id"])),
        )
        for index, case in enumerate(cases, start=1)
    ]
    storyboard_cases.extend(build_extra_storyboard_cases())
    if len(storyboard_cases) != 40:
        raise AssertionError(f"Storyboard dataset must contain exactly 40 cases, got {len(storyboard_cases)}")
    dataset = {
        "benchmark_version": BENCHMARK_VERSION,
        "frozen_before_storyboard_calls": True,
        "case_count": len(storyboard_cases),
        "cases": storyboard_cases,
    }
    dataset["dataset_sha256"] = content_hash(dataset["cases"])
    write_json(output_dir / "storyboard_dataset.json", dataset)
    _update_environment(
        output_dir,
        {
            "storyboard_dataset": {
                "path": str(output_dir / "storyboard_dataset.json"),
                "case_count": len(storyboard_cases),
                "sha256": dataset["dataset_sha256"],
                "source_breakdown": dict(Counter(item["source_origin"] for item in storyboard_cases)),
            }
        },
    )
    print(json.dumps({"case_count": 40, "dataset_sha256": dataset["dataset_sha256"]}), flush=True)


def run_reflexion_ab(output_dir: Path, *, workers: int, resume: bool) -> None:
    dataset_path = output_dir / "storyboard_dataset.json"
    if not dataset_path.is_file():
        raise FileNotFoundError("Run prepare-storyboard first")
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    cases = list(dataset["cases"])
    work_dir = output_dir / "_work" / "reflexion"
    work_dir.mkdir(parents=True, exist_ok=True)
    base_provider = provider_registry.get(ProviderType.LLM, settings.llm_provider)
    asset_report = {
        "planning_ready": True,
        "generation_ready": True,
        "inspection_level": "metadata_only",
        "missing_references": [],
        "conflicts": [],
    }

    def run_one(pair: tuple[int, dict[str, Any]]) -> dict[str, Any]:
        index, case = pair
        case_id = str(case["case_id"])
        path = work_dir / f"{index:02d}-{_filename(case_id)}.json"
        if resume and path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
        source = ShotDirectionInput.from_payload(case["source"])
        provider = RecordingProvider(base_provider)
        started = time.perf_counter()
        checkpoint: dict[str, Any] = {}
        failure: dict[str, Any] | None = None
        pipeline_returned = False
        try:
            result = run_shot_direction_pipeline(
                provider=provider,
                review_provider=provider,
                source=source,
                asset_report=asset_report,
                task_id=f"{output_dir.name}:reflexion:{case_id}",
                model=settings.llm_model,
                review_model=settings.llm_model,
                system_prompt=shot_direction_system_prompt(None),
                review_system_prompt=shot_direction_system_prompt(None),
                temperatures=STORYBOARD_TEMPERATURES,
                retry_not_submitted=False,
            )
            checkpoint = result.checkpoint
            pipeline_returned = True
        except ShotDirectionPipelineFailure as exc:
            checkpoint = exc.checkpoint
            failure = {
                "type": type(exc).__name__,
                "phase": exc.phase,
                "code": exc.code,
                "message": exc.message,
                "submission_state": exc.submission_state.value,
            }
        except Exception as exc:
            failure = {"type": type(exc).__name__, "message": str(exc)}

        draft = deepcopy(checkpoint.get("draft")) if isinstance(checkpoint.get("draft"), list) else None
        final = deepcopy(checkpoint.get("final")) if isinstance(checkpoint.get("final"), list) else draft
        review = deepcopy(checkpoint.get("review")) if isinstance(checkpoint.get("review"), dict) else {"issues": []}
        patch_value = deepcopy(checkpoint.get("patch")) if isinstance(checkpoint.get("patch"), dict) else None
        draft_eval = evaluate_storyboard(
            case=case,
            shots=draft,
            parse_error=None if draft is not None else _draft_failure(checkpoint, failure),
        )
        final_eval = evaluate_storyboard(
            case=case,
            shots=final,
            parse_error=None if final is not None else _draft_failure(checkpoint, failure),
        )
        preservation = _storyboard_preservation(draft or [], final or [], review, patch_value)
        must_fix = _verify_storyboard_must_fix(review, patch_value, final_eval)
        record = {
            "case_id": case_id,
            "dataset_order": index,
            "input": case,
            "raw_output": compact_checkpoint_raw(checkpoint),
            "parsed_output": {
                "draft": draft,
                "review": review,
                "patch": patch_value,
                "final": final,
                "draft_contract_report": checkpoint.get("draft_contract_report"),
                "final_contract_report": checkpoint.get("contract_report"),
            },
            "draft_evaluation": draft_eval,
            "final_evaluation": final_eval,
            "must_fix_evaluation": must_fix,
            "preservation_evaluation": preservation,
            "failure_reason": failure,
            "pipeline_returned": pipeline_returned,
            "model": settings.llm_model,
            "provider": settings.llm_provider,
            "capability_version": SHOT_DIRECTION_PIPELINE_VERSION,
            "prompt_version": f"shot_direction/prompts.py@sha256:{_sha256_file(SHOT_PROMPT_PATH)}",
            "latency_sec": round(time.perf_counter() - started, 6),
            "phase_calls": provider.calls,
            "token_usage": phase_token_usage(checkpoint),
            "retry_count": 0,
            "evidence_class": "live_real_llm",
        }
        write_json(path, record)
        return record

    case_records = _run_parallel(list(enumerate(cases, start=1)), run_one, workers=workers, label="reflexion")
    case_records.sort(key=lambda item: int(item["dataset_order"]))
    ab_rows: list[dict[str, Any]] = []
    for record in case_records:
        for arm, key in (("draft_only", "draft_evaluation"), ("reflexion_bounded_patch", "final_evaluation")):
            phase_names = {"storyboard_draft", "storyboard_structure_recovery"}
            if arm == "reflexion_bounded_patch":
                phase_names.update({"storyboard_reflection", "storyboard_patch"})
            ab_rows.append(
                {
                    "case_id": record["case_id"],
                    "dataset_order": record["dataset_order"],
                    "input": record["input"],
                    "arm": arm,
                    "raw_output": _filter_checkpoint(record["raw_output"], phase_names),
                    "parsed_output": (
                        record["parsed_output"]["draft"]
                        if arm == "draft_only"
                        else record["parsed_output"]["final"]
                    ),
                    "assertion_results": record[key],
                    "failure_reason": (
                        record["failure_reason"]
                        if arm == "reflexion_bounded_patch"
                        or record["parsed_output"]["draft"] is None
                        else None
                    ),
                    "model": record["model"],
                    "provider": record["provider"],
                    "capability_version": record["capability_version"],
                    "prompt_version": record["prompt_version"],
                    "latency_sec": _phase_latency(record["phase_calls"], phase_names),
                    "token_usage": phase_token_usage(_filter_checkpoint(record["raw_output"], phase_names)),
                    "retry_count": 0,
                    "review": record["parsed_output"]["review"] if arm != "draft_only" else None,
                    "patch": record["parsed_output"]["patch"] if arm != "draft_only" else None,
                    "must_fix_evaluation": record["must_fix_evaluation"] if arm != "draft_only" else None,
                    "preservation_evaluation": record["preservation_evaluation"] if arm != "draft_only" else None,
                    "evidence_class": record["evidence_class"],
                }
            )
    draft_gate_by_case = {
        str(row["case_id"]): bool(row["assertion_results"].get("gate_passed"))
        for row in ab_rows
        if row["arm"] == "draft_only"
    }
    for row in ab_rows:
        row["regression"] = bool(
            row["arm"] == "reflexion_bounded_patch"
            and draft_gate_by_case.get(str(row["case_id"]), False)
            and not row["assertion_results"].get("gate_passed")
        )
    write_json(output_dir / "reflexion_ab.json", ab_rows)
    csv_rows = [_ab_csv_row(item) for item in ab_rows]
    write_csv(
        output_dir / "reflexion_ab.csv",
        csv_rows,
        fieldnames=[
            "case_id",
            "dataset_order",
            "arm",
            "gate_passed",
            "dialogue_coverage_rate",
            "entity_reference_accuracy",
            "source_scene_coverage_rate",
            "schema_valid",
            "production_contract_valid",
            "shot_count_compliant",
            "duration_compliant",
            "internal_beat_count_compliant",
            "must_fix_count",
            "verified_must_fix_count",
            "verified_must_fix_resolved",
            "untargeted_shot_preservation_rate",
            "regression",
            "no_op_preserved",
            "failure_code",
            "latency_sec",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "provider",
            "model",
        ],
    )
    summary = _summarize_ab(ab_rows)
    summary["dataset_sha256"] = dataset["dataset_sha256"]
    summary["result_sha256"] = content_hash(ab_rows)
    write_json(output_dir / "reflexion_ab_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)


def run_bounded_patch(output_dir: Path, *, workers: int, resume: bool) -> None:
    cases = build_bounded_patch_cases(30)
    dataset = {
        "benchmark_version": BENCHMARK_VERSION,
        "frozen_before_provider_calls": True,
        "case_count": len(cases),
        "cases": cases,
        "dataset_sha256": content_hash(cases),
    }
    write_json(output_dir / "bounded_patch_dataset.json", dataset)
    _update_environment(
        output_dir,
        {
            "bounded_patch_dataset": {
                "path": str(output_dir / "bounded_patch_dataset.json"),
                "case_count": len(cases),
                "sha256": dataset["dataset_sha256"],
            }
        },
    )
    work_dir = output_dir / "_work" / "bounded_patch"
    work_dir.mkdir(parents=True, exist_ok=True)
    base_provider = provider_registry.get(ProviderType.LLM, settings.llm_provider)

    def run_one(pair: tuple[int, dict[str, Any]]) -> dict[str, Any]:
        index, case = pair
        path = work_dir / f"{index:02d}-{_filename(case['case_id'])}.json"
        if resume and path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
        source = ShotDirectionInput.from_payload(case["source"])
        provider = RecordingProvider(base_provider)
        prompt = build_shot_patch_prompt(source, draft=case["draft"], review=case["review"])
        started = time.perf_counter()
        response: ProviderResponse | None = None
        raw_text = ""
        parse_error: str | None = None
        patch_value: dict[str, Any] | None = None
        final: list[dict[str, Any]] | None = None
        failure: dict[str, Any] | None = None
        try:
            response = provider.submit(
                ProviderRequest(
                    project_id=source.project_id,
                    task_id=f"{output_dir.name}:bounded:{case['case_id']}",
                    model=settings.llm_model,
                    prompt=prompt,
                    system_prompt=shot_direction_system_prompt(None),
                    params={"temperature": STORYBOARD_TEMPERATURES["storyboard_patch"]},
                    metadata={
                        "stage": "shot_breakdown",
                        "phase": "storyboard_patch",
                        "pipeline_version": SHOT_DIRECTION_PIPELINE_VERSION,
                        "benchmark_version": BENCHMARK_VERSION,
                    },
                )
            )
            raw_text = str((response.raw_response or {}).get("text") or "")
            if response.status != ProviderStatus.SUCCEEDED:
                failure = {
                    "type": "ProviderFailure",
                    "code": response.error.error_code if response.error else response.status.value,
                    "message": response.error.error_message if response.error else "provider did not succeed",
                }
                parse_error = failure["message"]
            else:
                try:
                    parsed = parse_and_apply_shot_patch(
                        raw_text,
                        draft=case["draft"],
                        review=case["review"],
                        source=source,
                    )
                    patch_value = parsed.patch
                    final = parsed.shots
                except Exception as exc:
                    parse_error = str(exc)
                    failure = {"type": type(exc).__name__, "message": str(exc)}
        except Exception as exc:
            parse_error = str(exc)
            failure = {"type": type(exc).__name__, "message": str(exc)}

        evaluation = evaluate_bounded_patch(
            case=case,
            final_shots=final,
            patch=patch_value,
            parse_error=parse_error,
            raw_text=raw_text,
        )
        raw_response = deepcopy(response.raw_response) if response is not None else {}
        token_usage = _response_token_usage(raw_response)
        record = {
            "case_id": case["case_id"],
            "dataset_order": index,
            "input": case,
            "arm": "gold_critic_plus_real_bounded_patch",
            "raw_output": raw_response,
            "parsed_output": {"patch": patch_value, "final": final},
            "assertion_results": evaluation,
            "failure_reason": failure,
            "model": settings.llm_model,
            "provider": settings.llm_provider,
            "capability_version": SHOT_DIRECTION_PIPELINE_VERSION,
            "prompt_version": f"shot_direction/prompts.py@sha256:{_sha256_file(SHOT_PROMPT_PATH)}",
            "latency_sec": round(time.perf_counter() - started, 6),
            "phase_calls": provider.calls,
            "token_usage": token_usage,
            "retry_count": 0,
            "evidence_class": (
                "live_real_llm_with_controlled_gold_critic"
                if settings.llm_provider != "mock"
                else "built_in_mock_llm_with_controlled_gold_critic"
            ),
        }
        write_json(path, record)
        return record

    rows = _run_parallel(list(enumerate(cases, start=1)), run_one, workers=workers, label="bounded")
    rows.sort(key=lambda item: int(item["dataset_order"]))
    write_json(output_dir / "bounded_patch.json", rows)
    write_csv(
        output_dir / "bounded_patch.csv",
        [_bounded_csv_row(item) for item in rows],
        fieldnames=[
            "case_id",
            "dataset_order",
            "defect_type",
            "target_fix_success",
            "declared_resolved",
            "declared_but_unverified",
            "protected_field_preservation_rate",
            "out_of_bounds_edit_rate",
            "protected_field_opportunities",
            "protected_field_edits",
            "boundary_authorized",
            "full_rewrite_attempt",
            "regression",
            "parse_valid",
            "failure_reason",
            "latency_sec",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "provider",
            "model",
        ],
    )
    evaluations = [item["assertion_results"] for item in rows]
    opportunities = sum(int(item["protected_field_opportunities"]) for item in evaluations)
    edits = sum(int(item["protected_field_edits"]) for item in evaluations)
    summary = {
        "case_count": len(rows),
        "target_fix_success_rate": _rate(item["target_fix_success"] for item in evaluations),
        "declared_resolution_rate": _rate(item["declared_resolved"] for item in evaluations),
        "declared_but_unverified_count": sum(bool(item["declared_but_unverified"]) for item in evaluations),
        "protected_field_preservation_rate": ((opportunities - edits) / opportunities if opportunities else 1.0),
        "out_of_bounds_edit_rate": (edits / opportunities if opportunities else 0.0),
        "full_rewrite_rate": _rate(item["full_rewrite_attempt"] for item in evaluations),
        "regression_rate": _rate(item["regression"] for item in evaluations),
        "parse_valid_rate": _rate(item["parse_valid"] for item in evaluations),
        "protected_field_opportunities": opportunities,
        "protected_field_edits": edits,
        "failure_categories": dict(Counter(_failure_category(item) for item in rows if item["failure_reason"])),
        "benchmark_failure_categories": _bounded_failure_categories(evaluations),
        "dataset_sha256": dataset["dataset_sha256"],
        "result_sha256": content_hash(rows),
    }
    write_json(output_dir / "bounded_patch_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)


def aggregate_pipeline_eval(output_dir: Path) -> None:
    script_rows = json.loads((output_dir / "existing_31_eval.json").read_text(encoding="utf-8"))
    shot_rows = _load_work_rows(output_dir / "_work" / "reflexion")
    shot_by_id = {str(item["case_id"]): item for item in shot_rows}
    result: list[dict[str, Any]] = []
    for script in script_rows[:PIPELINE_EVAL_CASE_COUNT]:
        shot_id = f"existing31-{script['case_id']}"
        shot = shot_by_id.get(shot_id)
        script_contracts = _script_stage_contracts(script)
        shot_contracts = _shot_stage_contracts(shot)
        attempted = [*script_contracts, *shot_contracts]
        revision_rounds = sum(
            1
            for phase in (
                _phase_names(script.get("raw_output")),
                _phase_names((shot or {}).get("raw_output")),
            )
            for value in phase
            if value in {"script_patch", "storyboard_patch"}
        )
        final_gate_passed = bool(shot and shot["final_evaluation"].get("gate_passed"))
        pipeline_completed = bool(script["failure_reason"] is None and shot and shot.get("pipeline_returned"))
        failure_stage = _pipeline_failure_stage(script, shot)
        row = {
            "case_id": script["case_id"],
            "dataset_order": script["dataset_order"],
            "input": script["input"],
            "arm": "story_blueprint_to_storyboard",
            "raw_output": {
                "script": script.get("raw_output"),
                "storyboard": (shot or {}).get("raw_output"),
            },
            "parsed_output": {
                "script": script.get("parsed_output"),
                "storyboard": (shot or {}).get("parsed_output"),
            },
            "assertion_results": {
                "pipeline_completed": pipeline_completed,
                "stage_contract_validity": _rate(item["valid"] for item in attempted),
                "final_gate_passed": final_gate_passed,
                "revision_rounds": revision_rounds,
                "human_intervention_required": not pipeline_completed or not final_gate_passed,
                "stage_contracts": attempted,
            },
            "failure_reason": {
                "stage": failure_stage,
                "script": script.get("failure_reason"),
                "storyboard": (shot or {}).get("failure_reason"),
            } if failure_stage else None,
            "model": settings.llm_model,
            "provider": settings.llm_provider,
            "capability_version": {
                "script": SCRIPT_QUALITY_PIPELINE_VERSION,
                "storyboard": SHOT_DIRECTION_PIPELINE_VERSION,
            },
            "prompt_version": {
                "script": f"sha256:{_sha256_file(SCRIPT_PROMPT_PATH)}",
                "storyboard": f"sha256:{_sha256_file(SHOT_PROMPT_PATH)}",
            },
            "latency_sec": round(float(script.get("latency_sec") or 0) + float((shot or {}).get("latency_sec") or 0), 6),
            "token_usage": _add_usage(script.get("token_usage"), (shot or {}).get("token_usage")),
            "retry_count": 0,
            "evidence_class": (
                "live_real_llm_plus_deterministic_contracts"
                if settings.llm_provider != "mock"
                else "built_in_mock_llm_plus_deterministic_contracts"
            ),
        }
        result.append(row)

    write_json(output_dir / "pipeline_eval.json", result)
    write_csv(
        output_dir / "pipeline_eval.csv",
        [_pipeline_csv_row(item) for item in result],
        fieldnames=[
            "case_id",
            "dataset_order",
            "pipeline_completed",
            "stage_contract_validity",
            "final_gate_passed",
            "revision_rounds",
            "human_intervention_required",
            "failure_stage",
            "latency_sec",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "provider",
            "model",
        ],
    )
    turns = [int(item["assertion_results"]["revision_rounds"]) for item in result]
    stage_failures = Counter(
        str(item["failure_reason"]["stage"])
        for item in result
        if isinstance(item.get("failure_reason"), dict)
    )
    summary = {
        "case_count": len(result),
        "pipeline_completion_rate": _rate(item["assertion_results"]["pipeline_completed"] for item in result),
        "stage_contract_validity": (
            sum(float(item["assertion_results"]["stage_contract_validity"]) for item in result) / len(result)
            if result else 0.0
        ),
        "final_gate_pass_rate": _rate(item["assertion_results"]["final_gate_passed"] for item in result),
        "human_intervention_required_rate": _rate(item["assertion_results"]["human_intervention_required"] for item in result),
        "average_revision_turns": (sum(turns) / len(turns) if turns else 0.0),
        "p95_revision_turns": percentile(turns, 0.95),
        "max_revision_turns": max(turns) if turns else 0,
        "stage_failure_distribution": dict(stage_failures),
        "infinite_loop_observed": False,
        "bounded_retry_contract": "at most one script patch and one storyboard patch",
        "result_sha256": content_hash(result),
    }
    write_json(output_dir / "pipeline_eval_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)


def _run_parallel(
    items: list[Any],
    worker: Callable[[Any], dict[str, Any]],
    *,
    workers: int,
    label: str,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 6))) as executor:
        futures = {executor.submit(worker, item): item for item in items}
        for completed, future in enumerate(as_completed(futures), start=1):
            item = future.result()
            results.append(item)
            status = "ok" if item.get("failure_reason") is None else "failed"
            print(f"[{label}] {completed}/{len(items)} {item.get('case_id')} {status}", flush=True)
    return results


def _storyboard_preservation(
    draft: list[dict[str, Any]],
    final: list[dict[str, Any]],
    review: dict[str, Any],
    patch: dict[str, Any] | None,
) -> dict[str, Any]:
    allowed = {
        int(number)
        for issue in review.get("issues") or []
        if isinstance(issue, dict) and issue.get("severity") == "must_fix"
        for number in issue.get("shot_nos") or []
        if isinstance(number, int)
    }
    operations = patch.get("operations") if isinstance(patch, dict) else []
    structural_change = any(
        isinstance(item, dict) and item.get("op") in {"insert_after", "remove"}
        for item in operations or []
    )
    if structural_change:
        # Renumbering changes identity after an insert/remove. Preserve by content
        # fingerprint instead of the mutable ordinal.
        final_fingerprints = Counter(_shot_fingerprint(item) for item in final)
        opportunities = 0
        preserved = 0
        for shot in draft:
            if int(shot.get("shot_no") or 0) in allowed:
                continue
            opportunities += 1
            fingerprint = _shot_fingerprint(shot)
            if final_fingerprints[fingerprint] > 0:
                preserved += 1
                final_fingerprints[fingerprint] -= 1
    else:
        final_by_no = {int(item.get("shot_no") or 0): item for item in final}
        opportunities = 0
        preserved = 0
        for shot in draft:
            shot_no = int(shot.get("shot_no") or 0)
            if shot_no in allowed:
                continue
            opportunities += 1
            if _shot_fingerprint(shot) == _shot_fingerprint(final_by_no.get(shot_no, {})):
                preserved += 1
    must_fix_count = sum(
        1
        for item in review.get("issues") or []
        if isinstance(item, dict) and item.get("severity") == "must_fix"
    )
    exact_noop = _shot_fingerprint_list(draft) == _shot_fingerprint_list(final)
    return {
        "untargeted_shot_opportunities": opportunities,
        "untargeted_shots_preserved": preserved,
        "untargeted_shot_preservation_rate": preserved / opportunities if opportunities else 1.0,
        "no_op_opportunity": must_fix_count == 0,
        "no_op_preserved": exact_noop if must_fix_count == 0 else None,
        "structural_patch": structural_change,
    }


def _verify_storyboard_must_fix(
    review: dict[str, Any],
    patch: dict[str, Any] | None,
    final_eval: dict[str, Any],
) -> dict[str, Any]:
    issues = [
        item
        for item in review.get("issues") or []
        if isinstance(item, dict) and item.get("severity") == "must_fix"
    ]
    resolved_declared = set(str(value) for value in (patch or {}).get("resolved_issue_codes") or [])
    error_codes = {
        str(item.get("code") or "")
        for item in final_eval.get("contract_report", {}).get("errors") or []
        if isinstance(item, dict)
    }
    warning_codes = {
        str(item.get("code") or "")
        for item in final_eval.get("contract_report", {}).get("warnings") or []
        if isinstance(item, dict)
    }
    verified: list[dict[str, Any]] = []
    unverified: list[dict[str, Any]] = []
    for issue in issues:
        code = str(issue.get("code") or "")
        if code == "dialogue_coverage_missing":
            passed = final_eval.get("dialogue_coverage_rate") == 1.0
        elif code == "source_scene_coverage_missing":
            passed = final_eval.get("source_scene_coverage_rate") == 1.0
        elif code in {
            "shots_empty",
            "scene_reference_invalid",
            "entity_reference_invalid",
            "source_scene_invalid",
            "visible_action_missing",
            "storyboard_prompt_missing",
            "internal_beat_count_invalid",
            "duration_plan_missing",
            "duration_plan_out_of_range",
            "duration_rationale_missing",
            "duration_budget_out_of_range",
            "dialogue_duplicate",
            "dialogue_reference_invalid",
        }:
            passed = code not in error_codes and code not in warning_codes
        else:
            unverified.append({"code": code, "reason": "semantic critic issue has no independent gold assertion"})
            continue
        verified.append(
            {
                "code": code,
                "resolved": bool(passed),
                "declared_resolved": code in resolved_declared,
            }
        )
    return {
        "must_fix_count": len(issues),
        "declared_resolved_count": sum(str(item.get("code") or "") in resolved_declared for item in issues),
        "verified_must_fix_count": len(verified),
        "verified_resolved_count": sum(bool(item["resolved"]) for item in verified),
        "verified_resolution_rate": (
            sum(bool(item["resolved"]) for item in verified) / len(verified)
            if verified else None
        ),
        "verified": verified,
        "unverified": unverified,
    }


def _summarize_ab(rows: list[dict[str, Any]]) -> dict[str, Any]:
    arms = {
        arm: [row for row in rows if row["arm"] == arm]
        for arm in ("draft_only", "reflexion_bounded_patch")
    }
    metrics: dict[str, dict[str, Any]] = {}
    for arm, values in arms.items():
        required_dialogues = sum(int(row["assertion_results"].get("required_dialogue_count") or 0) for row in values)
        covered_dialogues = sum(int(row["assertion_results"].get("covered_dialogue_count") or 0) for row in values)
        required_references = sum(int(row["assertion_results"].get("required_reference_count") or 0) for row in values)
        correct_references = sum(int(row["assertion_results"].get("correct_reference_count") or 0) for row in values)
        metrics[arm] = {
            "case_count": len(values),
            "gate_pass_rate": _rate(row["assertion_results"].get("gate_passed") for row in values),
            "dialogue_coverage_rate": covered_dialogues / required_dialogues if required_dialogues else 1.0,
            "entity_reference_accuracy": correct_references / required_references if required_references else 1.0,
            "json_parse_validity": _rate(
                row["assertion_results"]["assertion_results"].get("schema_valid") for row in values
            ),
            "schema_validity": _rate(
                row["assertion_results"]["assertion_results"].get("production_contract_valid")
                for row in values
            ),
            "shot_count_compliance": _rate(row["assertion_results"]["assertion_results"].get("shot_count_compliant") for row in values),
            "duration_compliance": _rate(row["assertion_results"]["assertion_results"].get("duration_compliant") for row in values),
            "internal_beat_count_compliance": _rate(row["assertion_results"]["assertion_results"].get("internal_beat_count_compliant") for row in values),
            "quality_failure_categories": _quality_failure_categories(values),
        }
    final_values = arms["reflexion_bounded_patch"]
    verified_count = sum(int((row.get("must_fix_evaluation") or {}).get("verified_must_fix_count") or 0) for row in final_values)
    verified_resolved = sum(int((row.get("must_fix_evaluation") or {}).get("verified_resolved_count") or 0) for row in final_values)
    preservation_opportunities = sum(int((row.get("preservation_evaluation") or {}).get("untargeted_shot_opportunities") or 0) for row in final_values)
    preserved = sum(int((row.get("preservation_evaluation") or {}).get("untargeted_shots_preserved") or 0) for row in final_values)
    paired = {
        str(row["case_id"]): row
        for row in arms["draft_only"]
    }
    regressions = 0
    for final_row in final_values:
        draft_row = paired.get(str(final_row["case_id"]))
        if draft_row and draft_row["assertion_results"].get("gate_passed") and not final_row["assertion_results"].get("gate_passed"):
            regressions += 1
    noop_rows = [row for row in final_values if (row.get("preservation_evaluation") or {}).get("no_op_opportunity")]
    metrics["reflexion_bounded_patch"].update(
        {
            "must_fix_resolution_rate_verified": verified_resolved / verified_count if verified_count else None,
            "verified_must_fix_count": verified_count,
            "verified_must_fix_resolved": verified_resolved,
            "untargeted_shot_preservation_rate": preserved / preservation_opportunities if preservation_opportunities else 1.0,
            "out_of_scope_shot_modification_rate": (
                (preservation_opportunities - preserved) / preservation_opportunities
                if preservation_opportunities
                else 0.0
            ),
            "regression_rate": regressions / len(final_values) if final_values else 0.0,
            "no_op_preservation_rate": _rate((row.get("preservation_evaluation") or {}).get("no_op_preserved") for row in noop_rows) if noop_rows else None,
            "no_op_case_count": len(noop_rows),
        }
    )
    draft_gate = metrics["draft_only"]["gate_pass_rate"]
    final_gate = metrics["reflexion_bounded_patch"]["gate_pass_rate"]
    return {
        "arms": metrics,
        "absolute_gate_improvement": final_gate - draft_gate,
        "relative_gate_improvement": ((final_gate - draft_gate) / draft_gate if draft_gate else None),
        "failure_categories": dict(Counter(_failure_category(row) for row in final_values if row.get("failure_reason"))),
    }


def _script_stage_contracts(row: dict[str, Any]) -> list[dict[str, Any]]:
    parsed = row.get("parsed_output") if isinstance(row.get("parsed_output"), dict) else {}
    phases = _phase_names(row.get("raw_output"))
    result = [
        {"stage": "story_blueprint", "valid": isinstance(parsed.get("blueprint"), dict)},
        {"stage": "script_draft", "valid": isinstance(parsed.get("draft"), dict)},
        {"stage": "script_review", "valid": isinstance(parsed.get("review"), dict)},
    ]
    if "script_patch" in phases:
        result.append({"stage": "script_patch", "valid": isinstance(parsed.get("patch"), dict)})
    result.append({"stage": "script_final", "valid": isinstance(parsed.get("final"), dict)})
    return result


def _shot_stage_contracts(row: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(row, dict):
        return [
            {"stage": "storyboard_draft", "valid": False},
            {"stage": "storyboard_reflection", "valid": False},
            {"stage": "storyboard_final", "valid": False},
        ]
    parsed = row.get("parsed_output") if isinstance(row.get("parsed_output"), dict) else {}
    phases = _phase_names(row.get("raw_output"))
    result = [
        {"stage": "storyboard_draft", "valid": isinstance(parsed.get("draft"), list)},
        {"stage": "storyboard_reflection", "valid": isinstance(parsed.get("review"), dict)},
    ]
    if "storyboard_patch" in phases:
        result.append({"stage": "storyboard_patch", "valid": isinstance(parsed.get("patch"), dict)})
    result.append({"stage": "storyboard_final", "valid": isinstance(parsed.get("final"), list)})
    return result


def _pipeline_failure_stage(script: dict[str, Any], shot: dict[str, Any] | None) -> str | None:
    if script.get("failure_reason"):
        return str(script["failure_reason"].get("phase") or "script_pipeline")
    if not isinstance(shot, dict):
        return "storyboard_missing"
    if shot.get("failure_reason"):
        return str(shot["failure_reason"].get("phase") or "storyboard_pipeline")
    if not shot.get("pipeline_returned"):
        return "storyboard_pipeline"
    return None


def _quality_failure_categories(rows: list[dict[str, Any]]) -> dict[str, int]:
    failures: Counter[str] = Counter()
    for row in rows:
        evaluation = row.get("assertion_results") if isinstance(row.get("assertion_results"), dict) else {}
        assertions = evaluation.get("assertion_results") if isinstance(evaluation.get("assertion_results"), dict) else {}
        for key, passed in assertions.items():
            if not passed:
                failures[f"assertion:{key}"] += 1
        report = evaluation.get("contract_report") if isinstance(evaluation.get("contract_report"), dict) else {}
        for group in ("errors", "warnings"):
            for issue in report.get(group) or []:
                if isinstance(issue, dict):
                    failures[f"contract:{issue.get('code') or 'unknown'}"] += 1
        if row.get("failure_reason"):
            failures[f"pipeline:{_failure_category(row)}"] += 1
    return dict(failures.most_common())


def _bounded_failure_categories(evaluations: list[dict[str, Any]]) -> dict[str, int]:
    failures: Counter[str] = Counter()
    checks = {
        "target_fix_failed": lambda item: not item.get("target_fix_success"),
        "protected_fields_modified": lambda item: int(item.get("protected_field_edits") or 0) > 0,
        "boundary_unauthorized": lambda item: not item.get("boundary_authorized"),
        "full_rewrite_attempt": lambda item: bool(item.get("full_rewrite_attempt")),
        "regression": lambda item: bool(item.get("regression")),
        "parse_invalid": lambda item: not item.get("parse_valid"),
    }
    for name, failed in checks.items():
        failures[name] = sum(bool(failed(item)) for item in evaluations)
    return {key: value for key, value in failures.items() if value}


def _phase_names(checkpoint: Any) -> list[str]:
    responses = checkpoint.get("responses") if isinstance(checkpoint, dict) else None
    return list(responses) if isinstance(responses, dict) else []


def _filter_checkpoint(checkpoint: dict[str, Any], phases: set[str]) -> dict[str, Any]:
    value = deepcopy(checkpoint) if isinstance(checkpoint, dict) else {}
    responses = value.get("responses") if isinstance(value.get("responses"), dict) else {}
    value["responses"] = {key: item for key, item in responses.items() if key in phases}
    trace = value.get("pipeline_trace") if isinstance(value.get("pipeline_trace"), dict) else {}
    trace["phases"] = [
        item
        for item in trace.get("phases") or []
        if isinstance(item, dict) and item.get("phase") in phases
    ]
    value["pipeline_trace"] = trace
    for key in ("review", "patch", "final", "contract_report", "patch_applied", "patched_shot_nos"):
        if phases == {"storyboard_draft", "storyboard_structure_recovery"}:
            value.pop(key, None)
    return value


def _shot_fingerprint(shot: dict[str, Any]) -> str:
    value = deepcopy(shot)
    value.pop("shot_no", None)
    card = value.get("shot_card") if isinstance(value.get("shot_card"), dict) else {}
    for group in (card.get("beats"), (card.get("motion_timing") or {}).get("beats")):
        if isinstance(group, list):
            for item in group:
                if isinstance(item, dict):
                    item.pop("beat_id", None)
    return content_hash(value)


def _shot_fingerprint_list(shots: list[dict[str, Any]]) -> list[str]:
    return [_shot_fingerprint(item) for item in shots]


def _phase_latency(calls: list[dict[str, Any]], phases: set[str]) -> float:
    return round(sum(float(item.get("latency_sec") or 0) for item in calls if item.get("phase") in phases), 6)


def _response_token_usage(raw_response: dict[str, Any]) -> dict[str, int]:
    provider_response = raw_response.get("provider_response") if isinstance(raw_response.get("provider_response"), dict) else {}
    usage = provider_response.get("usage") if isinstance(provider_response.get("usage"), dict) else {}
    return {
        key: int(usage.get(key) or 0)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
    }


def _add_usage(left: Any, right: Any) -> dict[str, int]:
    left = left if isinstance(left, dict) else {}
    right = right if isinstance(right, dict) else {}
    return {
        key: int(left.get(key) or 0) + int(right.get(key) or 0)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
    }


def _script_csv_row(item: dict[str, Any]) -> dict[str, Any]:
    parsed = item.get("parsed_output") or {}
    review = parsed.get("review") if isinstance(parsed.get("review"), dict) else {}
    usage = item.get("token_usage") or {}
    failure = item.get("failure_reason") or {}
    return {
        "case_id": item["case_id"],
        "dataset_order": item["dataset_order"],
        "pipeline_completed": item["failure_reason"] is None,
        "legacy_eval_passed": item["assertion_results"].get("passed"),
        "quality_gate": (parsed.get("contract_report") or {}).get("quality_gate"),
        "patch_applied": bool(parsed.get("patch")),
        "must_fix_count": sum(
            1 for issue in review.get("issues") or []
            if isinstance(issue, dict) and issue.get("severity") == "must_fix"
        ),
        "failure_phase": failure.get("phase"),
        "failure_code": failure.get("code"),
        "latency_sec": item["latency_sec"],
        **usage,
        "provider": item["provider"],
        "model": item["model"],
    }


def _ab_csv_row(item: dict[str, Any]) -> dict[str, Any]:
    evaluation = item["assertion_results"]
    assertions = evaluation["assertion_results"]
    must_fix = item.get("must_fix_evaluation") or {}
    preservation = item.get("preservation_evaluation") or {}
    failure = item.get("failure_reason") or {}
    return {
        "case_id": item["case_id"],
        "dataset_order": item["dataset_order"],
        "arm": item["arm"],
        "gate_passed": evaluation["gate_passed"],
        "dialogue_coverage_rate": evaluation["dialogue_coverage_rate"],
        "entity_reference_accuracy": evaluation["entity_reference_accuracy"],
        "source_scene_coverage_rate": evaluation["source_scene_coverage_rate"],
        "schema_valid": assertions["schema_valid"],
        "production_contract_valid": assertions["production_contract_valid"],
        "shot_count_compliant": assertions["shot_count_compliant"],
        "duration_compliant": assertions["duration_compliant"],
        "internal_beat_count_compliant": assertions["internal_beat_count_compliant"],
        "must_fix_count": must_fix.get("must_fix_count"),
        "verified_must_fix_count": must_fix.get("verified_must_fix_count"),
        "verified_must_fix_resolved": must_fix.get("verified_resolved_count"),
        "untargeted_shot_preservation_rate": preservation.get("untargeted_shot_preservation_rate"),
        "regression": bool(item.get("regression")),
        "no_op_preserved": preservation.get("no_op_preserved"),
        "failure_code": failure.get("code"),
        "latency_sec": item["latency_sec"],
        **(item.get("token_usage") or {}),
        "provider": item["provider"],
        "model": item["model"],
    }


def _bounded_csv_row(item: dict[str, Any]) -> dict[str, Any]:
    evaluation = item["assertion_results"]
    return {
        "case_id": item["case_id"],
        "dataset_order": item["dataset_order"],
        "defect_type": item["input"]["defect_type"],
        **evaluation,
        "failure_reason": item.get("failure_reason"),
        "latency_sec": item["latency_sec"],
        **(item.get("token_usage") or {}),
        "provider": item["provider"],
        "model": item["model"],
    }


def _pipeline_csv_row(item: dict[str, Any]) -> dict[str, Any]:
    assertions = item["assertion_results"]
    failure = item.get("failure_reason") or {}
    return {
        "case_id": item["case_id"],
        "dataset_order": item["dataset_order"],
        **assertions,
        "failure_stage": failure.get("stage"),
        "latency_sec": item["latency_sec"],
        **(item.get("token_usage") or {}),
        "provider": item["provider"],
        "model": item["model"],
    }


def _load_work_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(item.read_text(encoding="utf-8")) for item in sorted(path.glob("*.json"))]


def _rate(values: Any) -> float:
    items = [bool(value) for value in values]
    return sum(items) / len(items) if items else 0.0


def _failure_category(item: dict[str, Any]) -> str:
    failure = item.get("failure_reason") or {}
    return str(failure.get("code") or failure.get("phase") or failure.get("type") or "unknown")


def _draft_failure(checkpoint: dict[str, Any], failure: dict[str, Any] | None) -> str:
    if failure:
        return str(failure.get("message") or failure.get("code") or "pipeline failure")
    return str(checkpoint.get("patch_error") or "draft unavailable")


def _filename(value: object) -> str:
    return "".join(character if character.isalnum() or character in "-_" else "_" for character in str(value))


def _llm_evidence_class() -> str:
    return "live_real_llm" if settings.llm_provider != "mock" else "built_in_mock_llm"


def _llm_evidence_label(suffix: str) -> str:
    prefix = "real LLM live" if settings.llm_provider != "mock" else "built-in mock LLM"
    return f"{prefix} {suffix}"


def _existing_eval_audit(cases: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "dataset_path": str(DEFAULT_CASES_PATH),
        "dataset_sha256": _sha256_file(DEFAULT_CASES_PATH),
        "introduced_by_commit": _git(
            ["log", "--diff-filter=A", "-1", "--format=%H", "--", str(DEFAULT_CASES_PATH)]
        ),
        "case_count": len(cases),
        "input_mode_counts": dict(Counter(str(case.get("input_mode") or "unknown") for case in cases)),
        "input_fields": ["title", "input_mode", "outline", "source_text"],
        "gold_assertion_fields": ["required_phrases", "required_tokens", "forbidden_tokens"],
        "focus_tags": dict(Counter(str(tag) for case in cases for tag in case.get("focus") or [])),
        "runner_path": str(API_ROOT / "scripts" / "run_script_quality_eval.py"),
        "evaluator_path": str(API_ROOT / "app" / "evals" / "script_quality.py"),
        "default_runner_provider_calls": 0,
        "pass_definition": (
            "parseable screenplay; every required phrase occurs in dialogue; every required token occurs "
            "in serialized scenes; no forbidden token occurs; deterministic augment_script_review emits no must_fix"
        ),
        "agent_or_model_fixed_by_dataset": False,
        "historical_raw_outputs_found_in_repository": False,
        "historical_pass_rate_available": False,
        "contains_draft_vs_reflection_patch_comparison": False,
        "current_version_reproducible_when_outputs_are_supplied": True,
        "audit_classification": "fixed offline input/assertion dataset plus evaluator, not 31 archived model runs",
        "known_metric_limitations": [
            "token/substring presence does not prove causal story quality or correct ownership",
            "focus tags are descriptive and are not individually scored",
            "the default runner does not invoke an Agent or model and ships without raw outputs",
            "the nominal silent-visual cases still inherit the screenplay contract requiring English dialogue",
        ],
    }


def _task_history_audit() -> dict[str, Any]:
    try:
        with SessionLocal() as db:
            rows = list(
                db.scalars(
                    select(GenerationTask).where(
                        GenerationTask.task_type.in_(("script_generation", "shot_breakdown"))
                    )
                ).all()
            )
    except Exception as exc:
        return {
            "read_succeeded": False,
            "error": f"{type(exc).__name__}: {exc}",
            "benchmark_reaggregation_allowed": False,
        }

    by_type: dict[str, Any] = {}
    for task_type in ("script_generation", "shot_breakdown"):
        values = [task for task in rows if task.task_type == task_type]
        current_results = [
            task
            for task in values
            if (task.result_payload or {}).get("pipeline_version")
            in {SCRIPT_QUALITY_PIPELINE_VERSION, SHOT_DIRECTION_PIPELINE_VERSION}
        ]
        by_type[task_type] = {
            "task_count": len(values),
            "status_counts": dict(Counter(task.status for task in values)),
            "provider_counts": dict(Counter(str(task.provider or "unknown") for task in values)),
            "model_counts": dict(Counter(str(task.model or "unknown") for task in values)),
            "current_pipeline_result_count": len(current_results),
            "current_pipeline_quality_gate_counts": dict(
                Counter(str((task.result_payload or {}).get("quality_gate") or "unknown") for task in current_results)
            ),
            "current_pipeline_patch_applied_counts": dict(
                Counter(str(bool((task.result_payload or {}).get("patch_applied"))).lower() for task in current_results)
            ),
        }
    return {
        "read_succeeded": True,
        "scoped_task_count": len(rows),
        "by_task_type": by_type,
        "benchmark_reaggregation_allowed": False,
        "reason": (
            "application task history is an uncontrolled mixture of inputs/models and has no mapping "
            "to the fixed 31-case dataset or paired Draft/Reflexion arms"
        ),
        "evidence_class": "read_only_uncontrolled_application_history_not_a_benchmark",
    }


def _git(args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def _sha256_text(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _package_version(name: str) -> str | None:
    try:
        return package_version(name)
    except PackageNotFoundError:
        return None


def _sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _origin(url: str | None) -> str | None:
    if not url:
        return None
    from urllib.parse import urlparse

    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else None


def _update_environment(output_dir: Path, value: dict[str, Any]) -> None:
    path = output_dir / "environment.json"
    environment = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    environment.update(value)
    write_json(path, environment)


if __name__ == "__main__":
    raise SystemExit(main())
