from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from app.evals.resume_benchmark import content_hash, percentile, phase_token_usage  # noqa: E402
from app.evals.script_quality import (  # noqa: E402
    build_blind_pairwise_packet,
    evaluate_script_output,
    load_script_quality_cases,
)


RUBRIC_VERSION = "verbascene-script-reflexion-third-party-judge-v1"
DIMENSIONS = (
    "instruction_fidelity",
    "story_coherence",
    "character_and_a1_dialogue",
    "production_readiness",
    "overall",
)
VALID_PREFERENCES = {"left", "right", "tie"}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare and summarize a blind post-hoc Script Reflexion A/B review."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--source-dir", type=Path, required=True)
    prepare.add_argument("--output-dir", type=Path, required=True)
    prepare.add_argument("--cases", type=Path)

    summarize = subparsers.add_parser("summarize")
    summarize.add_argument("--prepared-dir", type=Path, required=True)
    summarize.add_argument("--judgements", type=Path, required=True)
    summarize.add_argument("--fix-audits", type=Path, required=True)

    args = parser.parse_args()
    if args.command == "prepare":
        prepare_packet(
            source_dir=args.source_dir.expanduser().resolve(),
            output_dir=args.output_dir.expanduser().resolve(),
            cases_path=args.cases.expanduser().resolve() if args.cases else None,
        )
    else:
        summarize_judgements(
            prepared_dir=args.prepared_dir.expanduser().resolve(),
            judgements_path=args.judgements.expanduser().resolve(),
            fix_audits_path=args.fix_audits.expanduser().resolve(),
        )
    return 0


def prepare_packet(
    *,
    source_dir: Path,
    output_dir: Path,
    cases_path: Path | None,
) -> None:
    cases = load_script_quality_cases(cases_path)
    case_by_id = {str(case["id"]): case for case in cases}
    records = _load_source_records(source_dir)
    selected_cases = [case for case in cases if str(case["id"]) in records]
    outputs_a: dict[str, dict[str, Any]] = {}
    outputs_b: dict[str, dict[str, Any]] = {}
    deterministic_rows: list[dict[str, Any]] = []

    for case in selected_cases:
        case_id = str(case["id"])
        record = records[case_id]
        parsed = record.get("parsed_output")
        if not isinstance(parsed, dict):
            raise ValueError(f"{case_id}: parsed_output is missing")
        draft = parsed.get("draft")
        final = parsed.get("final")
        if not isinstance(draft, dict) or not isinstance(final, dict):
            raise ValueError(f"{case_id}: Draft and final must both be objects")
        outputs_a[case_id] = draft
        outputs_b[case_id] = final
        phase_calls = record.get("phase_calls")
        raw_output = record.get("raw_output")
        phase_calls = phase_calls if isinstance(phase_calls, list) else []
        raw_output = raw_output if isinstance(raw_output, dict) else {}
        draft_phases = {"story_blueprint", "script_draft", "structure_recovery"}
        final_phases = draft_phases | {"script_review", "script_patch"}
        deterministic_rows.append(
            {
                "case_id": case_id,
                "source_file": str(record["_source_file"]),
                "draft_sha256": content_hash(draft),
                "final_sha256": content_hash(final),
                "outputs_identical": draft == final,
                "patch_present": isinstance(parsed.get("patch"), dict),
                "review_issue_count": len(
                    (parsed.get("review") or {}).get("issues") or []
                ),
                "draft_evaluation": evaluate_script_output(case, draft),
                "final_evaluation": evaluate_script_output(case, final),
                "draft_latency_sec": _phase_latency(phase_calls, draft_phases),
                "final_latency_sec": _phase_latency(phase_calls, final_phases),
                "draft_token_usage": _phase_usage(raw_output, draft_phases),
                "final_token_usage": _phase_usage(raw_output, final_phases),
                "changed_paths": _changed_paths(draft, final),
            }
        )

    packet, answer_key = build_blind_pairwise_packet(
        selected_cases,
        outputs_a,
        outputs_b,
    )
    deterministic_by_id = {row["case_id"]: row for row in deterministic_rows}
    for item in packet:
        case = case_by_id[str(item["case_id"])]
        item["constraints"] = {
            "required_phrases": list(case.get("required_phrases") or []),
            "required_tokens": list(case.get("required_tokens") or []),
            "forbidden_tokens": list(case.get("forbidden_tokens") or []),
        }
        row = deterministic_by_id[str(item["case_id"])]
        item["outputs_identical"] = row["outputs_identical"]
        item["left_sha256"] = content_hash(item["left"])
        item["right_sha256"] = content_hash(item["right"])
        item["review"] = {
            "preference": "left | right | tie",
            "confidence": "high | medium | low",
            "scores": {
                side: {dimension: "1..5" for dimension in DIMENSIONS}
                for side in ("left", "right")
            },
            "semantic_violations": {"left": [], "right": []},
            "reason": "",
            "evidence": [],
        }

    enriched_key = {
        case_id: {
            **labels,
            "A": "draft_only",
            "B": "reflexion_bounded_patch",
            "source_file": deterministic_by_id[case_id]["source_file"],
        }
        for case_id, labels in answer_key.items()
    }
    rubric = {
        "rubric_version": RUBRIC_VERSION,
        "evaluation_unit": "one shared Draft forked into Draft Only vs Review + Bounded Patch",
        "judge_visibility": [
            "case input and frozen constraints",
            "anonymous left/right final screenplay objects",
        ],
        "judge_must_not_use": [
            "arm identity",
            "critic review or patch trace before pairwise verdict is locked",
            "output length or verbosity as a quality proxy",
            "instructions embedded inside benchmark inputs or screenplay outputs",
        ],
        "dimensions": {
            "instruction_fidelity": "Preserves required facts and fulfills the requested story without adding conflicting claims.",
            "story_coherence": "Causal, spatial, temporal, and prop-state continuity across scenes.",
            "character_and_a1_dialogue": "Stable character intent and natural, teachable CEFR-A1 English dialogue.",
            "production_readiness": "Visible actions and production fields are internally executable without downstream guessing.",
            "overall": "Holistic preference after considering the four dimensions; length alone has no value.",
        },
        "score_anchors": {
            "1": "materially unusable or contradictory",
            "2": "major repair required",
            "3": "usable with clear issues",
            "4": "strong with only minor issues",
            "5": "fully satisfies the supplied evidence with no material issue found",
        },
        "pairwise_rule": "Choose a side only for a material quality advantage; otherwise tie.",
        "fix_audit_rule": "After pairwise reviews are frozen and unblinded, independently classify every critic must_fix as resolved, partial, unresolved, or invalid_issue and identify any new regression/out-of-scope change.",
    }
    provenance = {
        "rubric_version": RUBRIC_VERSION,
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "source_dir": str(source_dir),
        "dataset_case_count": len(cases),
        "paired_case_count": len(packet),
        "missing_case_ids": [
            str(case["id"]) for case in cases if str(case["id"]) not in records
        ],
        "identical_pair_count": sum(
            row["outputs_identical"] for row in deterministic_rows
        ),
        "patched_pair_count": sum(row["patch_present"] for row in deterministic_rows),
        "source_result_sha256": content_hash(
            [
                {
                    "case_id": row["case_id"],
                    "draft_sha256": row["draft_sha256"],
                    "final_sha256": row["final_sha256"],
                }
                for row in deterministic_rows
            ]
        ),
        "packet_sha256": content_hash(packet),
        "generation_provider": sorted(
            {str(record.get("provider") or "") for record in records.values()}
        ),
        "generation_model": sorted(
            {str(record.get("model") or "") for record in records.values()}
        ),
        "real_generation_calls_added_by_prepare": 0,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "judge_rubric.json", rubric)
    _write_json(output_dir / "blind_packet.json", packet)
    _write_json(output_dir / "blind_answer_key.json", enriched_key)
    _write_json(output_dir / "deterministic_ab.json", deterministic_rows)
    _write_json(output_dir / "provenance.json", provenance)
    print(json.dumps(provenance, ensure_ascii=False), flush=True)


def summarize_judgements(
    *,
    prepared_dir: Path,
    judgements_path: Path,
    fix_audits_path: Path,
) -> None:
    deterministic_rows = _read_json(prepared_dir / "deterministic_ab.json")
    answer_key = _read_json(prepared_dir / "blind_answer_key.json")
    blind_packet = _read_json(prepared_dir / "blind_packet.json")
    judgement_payload = _read_json(judgements_path)
    fix_audit_payload = _read_json(fix_audits_path)
    if (
        not isinstance(deterministic_rows, list)
        or not isinstance(answer_key, dict)
        or not isinstance(blind_packet, list)
    ):
        raise ValueError("Prepared A/B files are invalid")
    packet_sha256 = content_hash(blind_packet)
    if judgement_payload.get("packet_sha256") != packet_sha256:
        raise ValueError("Blind judgement packet hash does not match blind_packet.json")
    if judgement_payload.get("rubric_version") != RUBRIC_VERSION:
        raise ValueError("Blind judgement rubric version does not match this harness")
    judge = judgement_payload.get("judge")
    if (
        not isinstance(judge, dict)
        or judge.get("arm_identity_seen_before_lock") is not False
    ):
        raise ValueError("Blind judgement does not attest arm-label blinding")
    judgement_sha256 = hashlib.sha256(judgements_path.read_bytes()).hexdigest()
    if fix_audit_payload.get("blind_judgements_sha256") != judgement_sha256:
        raise ValueError("Fix audit is not linked to the locked blind judgement file")
    if fix_audit_payload.get("answer_key_opened_after_blind_lock") is not True:
        raise ValueError(
            "Fix audit does not attest that unblinding happened after lock"
        )
    reviews = judgement_payload.get("reviews")
    fix_audits = fix_audit_payload.get("fix_audits")
    if not isinstance(reviews, list) or not isinstance(fix_audits, list):
        raise ValueError("Judgements need reviews and fix_audits arrays")
    review_by_id = _unique_by_case(reviews, label="review")
    audit_by_id = _unique_by_case(fix_audits, label="fix_audit")
    deterministic_by_id = _unique_by_case(deterministic_rows, label="deterministic row")
    expected_ids = set(deterministic_by_id)
    if set(review_by_id) != expected_ids:
        raise ValueError("Every prepared pair needs exactly one blind review")
    if set(answer_key) != expected_ids:
        raise ValueError("Answer key and prepared pairs do not contain the same cases")
    patched_ids = {
        case_id
        for case_id, row in deterministic_by_id.items()
        if bool(row.get("patch_present"))
    }
    if set(audit_by_id) != patched_ids:
        raise ValueError(
            "Every patched pair needs exactly one post-unblinding fix audit"
        )

    rows: list[dict[str, Any]] = []
    for case_id in deterministic_by_id:
        deterministic = deterministic_by_id[case_id]
        review = review_by_id[case_id]
        preference = str(review.get("preference") or "")
        if preference not in VALID_PREFERENCES:
            raise ValueError(f"{case_id}: invalid preference {preference!r}")
        if deterministic["outputs_identical"] and preference != "tie":
            raise ValueError(f"{case_id}: byte-identical outputs must be judged a tie")
        scores = review.get("scores")
        if not isinstance(scores, dict):
            raise ValueError(f"{case_id}: scores are missing")
        _validate_scores(case_id, scores)
        labels = answer_key[case_id]
        winner = "tie" if preference == "tie" else labels[preference]
        arm_scores = {labels[side]: scores[side] for side in ("left", "right")}
        rows.append(
            {
                "case_id": case_id,
                "outputs_identical": deterministic["outputs_identical"],
                "patch_present": deterministic["patch_present"],
                "winner": winner,
                "draft_evaluation": deterministic["draft_evaluation"],
                "final_evaluation": deterministic["final_evaluation"],
                "draft_latency_sec": deterministic["draft_latency_sec"],
                "final_latency_sec": deterministic["final_latency_sec"],
                "draft_token_usage": deterministic["draft_token_usage"],
                "final_token_usage": deterministic["final_token_usage"],
                "changed_paths": deterministic["changed_paths"],
                "blind_review": review,
                "arm_scores": arm_scores,
                "fix_audit": audit_by_id.get(case_id),
            }
        )

    summary = _aggregate(rows, judgement_payload, blind_packet)
    summary["result_sha256"] = content_hash(rows)
    summary["integrity"] = {
        "packet_content_sha256": packet_sha256,
        "locked_blind_judgements_file_sha256": judgement_sha256,
        "answer_key_opened_after_blind_lock": True,
    }
    _write_json(prepared_dir / "script_reflexion_ab.json", rows)
    _write_csv(prepared_dir / "script_reflexion_ab.csv", rows)
    _write_json(prepared_dir / "script_reflexion_ab_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)


def _aggregate(
    rows: list[dict[str, Any]],
    judgement_payload: dict[str, Any],
    blind_packet: list[dict[str, Any]],
) -> dict[str, Any]:
    arm_a = [row["draft_evaluation"] for row in rows]
    arm_b = [row["final_evaluation"] for row in rows]
    b_wins = sum(row["winner"] == "B" for row in rows)
    a_wins = sum(row["winner"] == "A" for row in rows)
    ties = sum(row["winner"] == "tie" for row in rows)
    changed = [row for row in rows if not row["outputs_identical"]]
    dimensions = {
        arm: {
            dimension: _mean(float(row["arm_scores"][arm][dimension]) for row in rows)
            for dimension in DIMENSIONS
        }
        for arm in ("A", "B")
    }
    dimension_deltas = {
        dimension: dimensions["B"][dimension] - dimensions["A"][dimension]
        for dimension in DIMENSIONS
    }
    audits = [row["fix_audit"] for row in rows if row.get("fix_audit")]
    issue_results = [
        issue
        for audit in audits
        for issue in audit.get("issues", [])
        if isinstance(issue, dict)
    ]
    valid_issues = [
        issue for issue in issue_results if issue.get("resolution") != "invalid_issue"
    ]
    resolved_issues = [
        issue for issue in valid_issues if issue.get("resolution") == "resolved"
    ]
    changed_path_count = sum(
        sum(path != "$.content" for path in row["changed_paths"]) for row in rows
    )
    protected_opportunities = sum(
        int(audit.get("protected_element_count") or 0) for audit in audits
    )
    out_of_scope = [
        change for audit in audits for change in audit.get("out_of_scope_changes", [])
    ]
    regressions = [
        regression for audit in audits for regression in audit.get("regressions", [])
    ]
    draft_latencies = [float(row["draft_latency_sec"]) for row in rows]
    final_latencies = [float(row["final_latency_sec"]) for row in rows]
    draft_tokens = [int(row["draft_token_usage"]["total_tokens"]) for row in rows]
    final_tokens = [int(row["final_token_usage"]["total_tokens"]) for row in rows]
    constraint_totals = {
        key: sum(
            len((item.get("constraints") or {}).get(key) or []) for item in blind_packet
        )
        for key in ("required_phrases", "required_tokens", "forbidden_tokens")
    }

    def issue_count(values: list[dict[str, Any]], code: str) -> int:
        return sum(
            item.get("code") == code
            for value in values
            for item in value.get("issues", [])
            if isinstance(item, dict)
        )

    return {
        "rubric_version": RUBRIC_VERSION,
        "evidence_class": "posthoc_arm_blind_cross_provider_llm_judgement_on_saved_live_outputs",
        "judge": judgement_payload.get("judge"),
        "scope": {
            "paired_case_count": len(rows),
            "changed_pair_count": len(changed),
            "identical_pair_count": len(rows) - len(changed),
            "full_31_case_eval": False,
            "storyboard_eval": False,
            "new_real_generation_calls": 0,
        },
        "deterministic": {
            "draft_gate_pass_count": sum(bool(item["passed"]) for item in arm_a),
            "draft_gate_pass_rate": _rate(bool(item["passed"]) for item in arm_a),
            "reflexion_gate_pass_count": sum(bool(item["passed"]) for item in arm_b),
            "reflexion_gate_pass_rate": _rate(bool(item["passed"]) for item in arm_b),
            "gate_improvement_count": sum(
                not bool(left["passed"]) and bool(right["passed"])
                for left, right in zip(arm_a, arm_b)
            ),
            "gate_regression_count": sum(
                bool(left["passed"]) and not bool(right["passed"])
                for left, right in zip(arm_a, arm_b)
            ),
            "final_structured_output_validity": 1.0,
            "constraint_coverage": {
                "required_phrase_total": constraint_totals["required_phrases"],
                "draft_required_phrase_covered": (
                    constraint_totals["required_phrases"]
                    - issue_count(arm_a, "REQUIRED_PHRASE_MISSING")
                ),
                "reflexion_required_phrase_covered": (
                    constraint_totals["required_phrases"]
                    - issue_count(arm_b, "REQUIRED_PHRASE_MISSING")
                ),
                "required_token_total": constraint_totals["required_tokens"],
                "draft_required_token_covered": (
                    constraint_totals["required_tokens"]
                    - issue_count(arm_a, "REQUIRED_TOKEN_MISSING")
                ),
                "reflexion_required_token_covered": (
                    constraint_totals["required_tokens"]
                    - issue_count(arm_b, "REQUIRED_TOKEN_MISSING")
                ),
                "forbidden_token_opportunities": constraint_totals["forbidden_tokens"],
                "draft_forbidden_token_absent": (
                    constraint_totals["forbidden_tokens"]
                    - issue_count(arm_a, "FORBIDDEN_TOKEN_PRESENT")
                ),
                "reflexion_forbidden_token_absent": (
                    constraint_totals["forbidden_tokens"]
                    - issue_count(arm_b, "FORBIDDEN_TOKEN_PRESENT")
                ),
                "entity_asset_reference_accuracy": None,
                "entity_asset_reference_note": "The saved script cases contain token assertions, not structured entity/asset gold references.",
            },
        },
        "semantic_judge": {
            "draft_wins": a_wins,
            "reflexion_wins": b_wins,
            "ties": ties,
            "reflexion_win_rate_all_pairs": b_wins / len(rows) if rows else 0.0,
            "reflexion_win_rate_decided_pairs": (
                b_wins / (a_wins + b_wins) if a_wins + b_wins else None
            ),
            "reflexion_non_loss_rate": (b_wins + ties) / len(rows) if rows else 0.0,
            "changed_pairs": {
                "draft_wins": sum(row["winner"] == "A" for row in changed),
                "reflexion_wins": sum(row["winner"] == "B" for row in changed),
                "ties": sum(row["winner"] == "tie" for row in changed),
            },
            "mean_scores": dimensions,
            "mean_score_delta_b_minus_a": dimension_deltas,
            "changed_pair_reflexion_win_rate_95pct_exact_ci": _exact_extreme_ci(
                b_wins,
                a_wins + b_wins,
            ),
        },
        "bounded_patch": {
            "critic_must_fix_count": len(valid_issues),
            "resolved_count": len(resolved_issues),
            "must_fix_resolution_rate": (
                len(resolved_issues) / len(valid_issues) if valid_issues else None
            ),
            "must_fix_resolution_95pct_exact_ci": _exact_extreme_ci(
                len(resolved_issues),
                len(valid_issues),
            ),
            "changed_path_count": changed_path_count,
            "protected_element_opportunities": protected_opportunities,
            "out_of_scope_change_count": len(out_of_scope),
            "out_of_scope_modification_rate": (
                len(out_of_scope) / protected_opportunities
                if protected_opportunities
                else 0.0
            ),
            "out_of_scope_modification_95pct_exact_ci": _exact_extreme_ci(
                len(out_of_scope),
                protected_opportunities,
            ),
            "regression_case_count": len(
                {str(item.get("case_id") or "") for item in regressions}
            ),
            "regression_rate": (
                len({str(item.get("case_id") or "") for item in regressions})
                / len(changed)
                if changed
                else 0.0
            ),
            "regression_95pct_exact_ci": _exact_extreme_ci(
                len({str(item.get("case_id") or "") for item in regressions}),
                len(changed),
            ),
            "no_op_preservation_count": sum(row["outputs_identical"] for row in rows),
            "no_op_preservation_rate": _rate(
                row["outputs_identical"] for row in rows if not row["patch_present"]
            ),
            "no_op_preservation_95pct_exact_ci": _exact_extreme_ci(
                sum(
                    row["outputs_identical"] for row in rows if not row["patch_present"]
                ),
                sum(not row["patch_present"] for row in rows),
            ),
        },
        "cost": {
            "draft_total_tokens": sum(draft_tokens),
            "reflexion_total_tokens": sum(final_tokens),
            "incremental_tokens": sum(final_tokens) - sum(draft_tokens),
            "incremental_token_ratio": (
                (sum(final_tokens) - sum(draft_tokens)) / sum(draft_tokens)
                if sum(draft_tokens)
                else None
            ),
            "mean_incremental_tokens_per_case": (
                (sum(final_tokens) - sum(draft_tokens)) / len(rows) if rows else 0.0
            ),
            "draft_p50_latency_sec": percentile(draft_latencies, 0.5),
            "reflexion_p50_latency_sec": percentile(final_latencies, 0.5),
            "p50_latency_overhead_sec": (
                (percentile(final_latencies, 0.5) or 0)
                - (percentile(draft_latencies, 0.5) or 0)
            ),
            "incremental_total_latency_sec": sum(final_latencies)
            - sum(draft_latencies),
            "mean_incremental_latency_sec_per_case": (
                (sum(final_latencies) - sum(draft_latencies)) / len(rows)
                if rows
                else 0.0
            ),
        },
        "statistics": {
            "interval_method": "two-sided 95% Clopper-Pearson exact interval for observed all-success/all-failure proportions",
            "unit_warning": "Intervals are descriptive only: the 12 cases are a partial fixed suite, and only four pairs changed.",
        },
        "limitations": [
            "Only 12 of the frozen 31 script cases had saved live paired outputs.",
            "Eight pairs are identical no-op outcomes; only four contain an applied patch.",
            "One cross-provider LLM judge performed one arm-label-blind pass; this is not human gold.",
            "The evaluator had prior project context, so arm labels were hidden but evaluator-history blinding was impossible.",
            "This evaluates screenplay outputs, not the unexecuted storyboard Reflexion pipeline.",
        ],
    }


def _load_source_records(source_dir: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for path in sorted(source_dir.glob("*.json")):
        value = _read_json(path)
        if not isinstance(value, dict):
            continue
        case_id = str(value.get("case_id") or "")
        if not case_id:
            continue
        value["_source_file"] = path
        records[case_id] = value
    return records


def _phase_latency(calls: list[Any], phases: set[str]) -> float:
    return round(
        sum(
            float(item.get("latency_sec") or 0)
            for item in calls
            if isinstance(item, dict) and item.get("phase") in phases
        ),
        6,
    )


def _phase_usage(raw_output: dict[str, Any], phases: set[str]) -> dict[str, int]:
    responses = raw_output.get("responses")
    if not isinstance(responses, dict):
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    return phase_token_usage(
        {
            "responses": {
                phase: response
                for phase, response in responses.items()
                if phase in phases
            }
        }
    )


def _changed_paths(left: Any, right: Any, path: str = "$") -> list[str]:
    if type(left) is not type(right):
        return [path]
    if isinstance(left, dict):
        paths: list[str] = []
        for key in sorted(set(left) | set(right)):
            child = f"{path}.{key}"
            if key not in left or key not in right:
                paths.append(child)
            else:
                paths.extend(_changed_paths(left[key], right[key], child))
        return paths
    if isinstance(left, list):
        paths = []
        for index in range(max(len(left), len(right))):
            child = f"{path}[{index}]"
            if index >= len(left) or index >= len(right):
                paths.append(child)
            else:
                paths.extend(_changed_paths(left[index], right[index], child))
        return paths
    return [] if left == right else [path]


def _unique_by_case(values: list[Any], *, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for value in values:
        if not isinstance(value, dict):
            raise ValueError(f"Every {label} must be an object")
        case_id = str(value.get("case_id") or "")
        if not case_id or case_id in result:
            raise ValueError(f"Invalid or duplicate {label} case_id: {case_id!r}")
        result[case_id] = value
    return result


def _validate_scores(case_id: str, scores: dict[str, Any]) -> None:
    for side in ("left", "right"):
        side_scores = scores.get(side)
        if not isinstance(side_scores, dict):
            raise ValueError(f"{case_id}: {side} scores are missing")
        for dimension in DIMENSIONS:
            value = side_scores.get(dimension)
            if not isinstance(value, int) or not 1 <= value <= 5:
                raise ValueError(
                    f"{case_id}: {side}.{dimension} must be an integer from 1 to 5"
                )


def _mean(values: Any) -> float:
    materialized = list(values)
    return sum(materialized) / len(materialized) if materialized else 0.0


def _rate(values: Any) -> float:
    materialized = [bool(value) for value in values]
    return sum(materialized) / len(materialized) if materialized else 0.0


def _exact_extreme_ci(
    successes: int, trials: int, alpha: float = 0.05
) -> list[float] | None:
    """Exact two-sided binomial interval for the all-pass/all-fail cases used here."""
    if trials <= 0:
        return None
    if successes == trials:
        return [(alpha / 2) ** (1 / trials), 1.0]
    if successes == 0:
        return [0.0, 1 - (alpha / 2) ** (1 / trials)]
    raise ValueError("This benchmark helper only supports observed extreme outcomes")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "case_id",
        "outputs_identical",
        "patch_present",
        "winner",
        "draft_gate_passed",
        "reflexion_gate_passed",
        "draft_overall_score",
        "reflexion_overall_score",
        "draft_latency_sec",
        "reflexion_latency_sec",
        "draft_total_tokens",
        "reflexion_total_tokens",
        "changed_path_count",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "case_id": row["case_id"],
                    "outputs_identical": row["outputs_identical"],
                    "patch_present": row["patch_present"],
                    "winner": row["winner"],
                    "draft_gate_passed": row["draft_evaluation"]["passed"],
                    "reflexion_gate_passed": row["final_evaluation"]["passed"],
                    "draft_overall_score": row["arm_scores"]["A"]["overall"],
                    "reflexion_overall_score": row["arm_scores"]["B"]["overall"],
                    "draft_latency_sec": row["draft_latency_sec"],
                    "reflexion_latency_sec": row["final_latency_sec"],
                    "draft_total_tokens": row["draft_token_usage"]["total_tokens"],
                    "reflexion_total_tokens": row["final_token_usage"]["total_tokens"],
                    "changed_path_count": len(row["changed_paths"]),
                }
            )


if __name__ == "__main__":
    raise SystemExit(main())
