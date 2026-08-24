import unittest

from app.evals.script_quality import (
    build_blind_pairwise_packet,
    evaluate_script_output,
    load_script_quality_cases,
)


class ScriptQualityEvalTests(unittest.TestCase):
    def test_fixed_suite_contains_thirty_unique_cross_category_cases(self):
        cases = load_script_quality_cases()

        self.assertGreaterEqual(len(cases), 30)
        self.assertEqual(len({case["id"] for case in cases}), len(cases))
        focus = {item for case in cases for item in case["focus"]}
        self.assertIn("source_fidelity", focus)
        self.assertIn("prompt_injection", focus)
        self.assertIn("a1_naturalness", focus)
        self.assertIn("state_continuity", focus)

    def test_offline_contract_detects_missing_required_phrase_without_provider_calls(self):
        case = next(case for case in load_script_quality_cases() if case["id"] == "explicit-three-phrases")
        payload = {
            "scenes": [
                {
                    "scene_no": 1,
                    "title": "最后一块蓝色积木",
                    "visible_action": "Mia把蓝色积木递给Leo，两人一起完成塔顶。",
                    "story_purpose": "完成合作",
                    "start_state": "塔顶缺一块积木。",
                    "end_state": "蓝色积木已经放稳。",
                    "dialogues": [
                        {"speaker": "Mia", "text": "Can I use it?", "emotion": "询问"},
                        {"speaker": "Leo", "text": "Here you are.", "emotion": "友好"},
                    ],
                }
            ]
        }

        result = evaluate_script_output(case, payload)

        self.assertFalse(result["passed"])
        self.assertIn(
            {"code": "REQUIRED_PHRASE_MISSING", "detail": "Wait, please."},
            result["issues"],
        )

    def test_pairwise_packet_is_blind_and_answer_key_is_separate(self):
        cases = load_script_quality_cases()[:2]
        outputs_a = {case["id"]: {"scenes": [{"visible_action": "版本A"}]} for case in cases}
        outputs_b = {case["id"]: {"scenes": [{"visible_action": "版本B"}]} for case in cases}

        packet, answer_key = build_blind_pairwise_packet(cases, outputs_a, outputs_b)

        self.assertEqual(len(packet), 2)
        self.assertEqual(set(answer_key), {case["id"] for case in cases})
        self.assertNotIn("source", packet[0]["left"])
        self.assertNotIn("answer_key", packet[0])
