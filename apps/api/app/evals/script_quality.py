from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from app.agents.script_contracts import augment_script_review
from app.agents.script_screenplay import parse_screenplay_response


DEFAULT_CASES_PATH = Path(__file__).resolve().parents[2] / "evals" / "script_quality_cases.json"


def load_script_quality_cases(path: Path | None = None) -> list[dict[str, Any]]:
    payload = json.loads((path or DEFAULT_CASES_PATH).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Script quality cases must be a JSON array")
    cases = [dict(item) for item in payload if isinstance(item, dict)]
    ids = [str(case.get("id") or "") for case in cases]
    if any(not case_id for case_id in ids) or len(ids) != len(set(ids)):
        raise ValueError("Every script quality case needs a unique non-empty id")
    return cases


def evaluate_script_output(case: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    screenplay = parse_screenplay_response(json.dumps(payload, ensure_ascii=False))
    serialized = json.dumps(screenplay["scenes"], ensure_ascii=False).casefold()
    dialogue_text = "\n".join(item["text"] for item in screenplay["dialogues"]).casefold()
    issues: list[dict[str, str]] = []

    for phrase in _string_list(case.get("required_phrases")):
        if phrase.casefold() not in dialogue_text:
            issues.append({"code": "REQUIRED_PHRASE_MISSING", "detail": phrase})
    for token in _string_list(case.get("required_tokens")):
        if token.casefold() not in serialized:
            issues.append({"code": "REQUIRED_TOKEN_MISSING", "detail": token})
    for token in _string_list(case.get("forbidden_tokens")):
        if token.casefold() in serialized:
            issues.append({"code": "FORBIDDEN_TOKEN_PRESENT", "detail": token})

    contract_review = augment_script_review(
        {
            "issues": [],
        },
        draft_scenes=screenplay["scenes"],
        blueprint={"required_phrases": _string_list(case.get("required_phrases"))},
    )
    for item in contract_review["issues"]:
        if item.get("severity") != "must_fix":
            continue
        code = str(item.get("code") or "CONTENT_CONTRACT")
        if not any(issue["code"] == code for issue in issues):
            issues.append({"code": code, "detail": str(item.get("problem") or "")})

    return {
        "case_id": str(case["id"]),
        "passed": not issues,
        "issues": issues,
        "scene_count": len(screenplay["scenes"]),
        "dialogue_count": len(screenplay["dialogues"]),
    }


def build_blind_pairwise_packet(
    cases: list[dict[str, Any]],
    outputs_a: dict[str, dict[str, Any]],
    outputs_b: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, str]]]:
    packet: list[dict[str, Any]] = []
    answer_key: dict[str, dict[str, str]] = {}
    for case in cases:
        case_id = str(case["id"])
        if case_id not in outputs_a or case_id not in outputs_b:
            continue
        swap = int(sha256(case_id.encode("utf-8")).hexdigest()[-1], 16) % 2 == 1
        left_label, right_label = ("B", "A") if swap else ("A", "B")
        left = outputs_b[case_id] if swap else outputs_a[case_id]
        right = outputs_a[case_id] if swap else outputs_b[case_id]
        packet.append(
            {
                "case_id": case_id,
                "title": case.get("title"),
                "focus": case.get("focus"),
                "input": {
                    "input_mode": case.get("input_mode"),
                    "outline": case.get("outline"),
                    "source_text": case.get("source_text"),
                },
                "left": left,
                "right": right,
                "review": {
                    "preference": "left | right | tie",
                    "causal_story": "",
                    "character_and_dialogue": "",
                    "production_readiness": "",
                    "reason": "",
                },
            }
        )
        answer_key[case_id] = {"left": left_label, "right": right_label}
    return packet, answer_key


def load_output_directory(path: Path) -> dict[str, dict[str, Any]]:
    outputs: dict[str, dict[str, Any]] = {}
    for file_path in sorted(path.glob("*.json")):
        value = json.loads(file_path.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            outputs[file_path.stem] = value
    return outputs


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
