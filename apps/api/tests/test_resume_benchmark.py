from copy import deepcopy
from pathlib import Path
import unittest

from app.evals.resume_benchmark import (
    build_bounded_patch_cases,
    build_extra_storyboard_cases,
    content_hash,
    evaluate_bounded_patch,
    storyboard_case_from_script_result,
)
from app.evals.script_quality import DEFAULT_CASES_PATH, load_script_quality_cases
from app.production.shot_direction.contracts import (
    ShotDirectionInput,
    build_shot_contract_report,
)


class ResumeBenchmarkDatasetTests(unittest.TestCase):
    def test_existing_eval_source_is_the_unchanged_31_case_dataset(self):
        cases = load_script_quality_cases()

        self.assertEqual(len(cases), 31)
        self.assertEqual(
            _sha256(DEFAULT_CASES_PATH),
            "377e6f2a5c7c8f8aacb9d717d61705aae23c58f73dfc9cf095e1bd4dec8c2eac",
        )

    def test_storyboard_dataset_expands_the_existing_cases_to_exactly_40(self):
        legacy = load_script_quality_cases()
        derived = [
            storyboard_case_from_script_result(case, index=index, script_result=None)
            for index, case in enumerate(legacy, start=1)
        ]
        extras = build_extra_storyboard_cases()

        self.assertEqual(len(derived), 31)
        self.assertEqual(len(extras), 9)
        self.assertEqual(len({item["case_id"] for item in [*derived, *extras]}), 40)
        for item in [*derived, *extras]:
            ShotDirectionInput.from_payload(item["source"])


class ResumeBenchmarkBoundedPatchTests(unittest.TestCase):
    def test_clean_references_are_valid_and_only_change_gold_allowed_fields(self):
        cases = build_bounded_patch_cases(30)

        self.assertEqual(len(cases), 30)
        self.assertEqual(len({item["case_id"] for item in cases}), 30)
        self.assertEqual(len({item["defect_type"] for item in cases}), 10)
        self.assertEqual(
            content_hash(cases),
            "e839602167a49efbef13ea83c2759258ee308c5c5712b813d87fc5b19439ad63",
        )
        for case in cases:
            source = ShotDirectionInput.from_payload(case["source"])
            self.assertTrue(
                build_shot_contract_report(shots=case["clean_reference"], source=source)["valid"],
                case["case_id"],
            )
            evaluation = evaluate_bounded_patch(
                case=case,
                final_shots=case["clean_reference"],
                patch={"resolved_issue_codes": [case["gold"]["issue_code"]], "operations": []},
                parse_error=None,
                raw_text="{}",
            )
            self.assertTrue(evaluation["target_fix_success"], case["case_id"])
            self.assertEqual(evaluation["protected_field_edits"], 0, case["case_id"])

    def test_independent_diff_gate_detects_a_regressive_untargeted_edit(self):
        case = build_bounded_patch_cases(1)[0]
        final = deepcopy(case["clean_reference"])
        final[0]["description"] = "An unrelated rewrite."

        evaluation = evaluate_bounded_patch(
            case=case,
            final_shots=final,
            patch={"resolved_issue_codes": [case["gold"]["issue_code"]], "operations": []},
            parse_error=None,
            raw_text="{}",
        )

        self.assertTrue(evaluation["target_fix_success"])
        self.assertGreater(evaluation["protected_field_edits"], 0)
        self.assertTrue(evaluation["regression"])


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    unittest.main()
